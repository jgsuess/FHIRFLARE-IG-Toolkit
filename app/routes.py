from flask import Blueprint, render_template, redirect, url_for, flash, request, send_from_directory, abort
from flask_login import login_required, current_user
from app import db
from app.models import FHIRApp, Category, OSSupport, FHIRSupport, PricingLicense, DesignedFor, User
from app.forms import FHIRAppForm, GalleryFilterForm, CategoryForm, UserEditForm
from sqlalchemy import or_
import os
import logging
from werkzeug.utils import secure_filename
import uuid

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

gallery_bp = Blueprint('gallery', __name__)

UPLOAD_FOLDER = '/app/uploads/'
ALLOWED_EXTENSIONS = {'jpg', 'png'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@gallery_bp.route('/uploads/<path:filename>')
def uploaded_file(filename):
    absolute_upload_folder = os.path.abspath(UPLOAD_FOLDER)
    logger.debug(f"Attempting to serve file: {filename} from {absolute_upload_folder}")
    try:
        return send_from_directory(absolute_upload_folder, filename)
    except FileNotFoundError:
        logger.error(f"File not found: {os.path.join(absolute_upload_folder, filename)}")
        abort(404)

@gallery_bp.route('/')
def index():
    return redirect(url_for('gallery.landing'))

@gallery_bp.route('/landing')
def landing():
    featured_apps = FHIRApp.query.order_by(FHIRApp.registration_date.desc()).limit(6).all()
    categories = Category.query.all()
    return render_template('landing.html', featured_apps=featured_apps, categories=categories)

@gallery_bp.route('/gallery', methods=['GET', 'POST'])
def gallery():
    form = GalleryFilterForm()
    query = FHIRApp.query
    filter_params = {}

    search_term = request.args.get('search', '').strip()
    if search_term:
        query = query.filter(
            or_(
                FHIRApp.name.ilike(f'%{search_term}%'),
                FHIRApp.description.ilike(f'%{search_term}%'),
                FHIRApp.developer.ilike(f'%{search_term}%')
            )
        )
        filter_params['search'] = search_term

    category_ids = request.args.getlist('category', type=int)
    os_support_ids = request.args.getlist('os_support', type=int)
    fhir_support_ids = request.args.getlist('fhir_support', type=int)
    pricing_license_ids = request.args.getlist('pricing_license', type=int)
    designed_for_ids = request.args.getlist('designed_for', type=int)

    if category_ids:
        query = query.filter(or_(*[FHIRApp.categories.contains(str(cid)) for cid in category_ids]))
        filter_params['category'] = category_ids
    if os_support_ids:
        query = query.filter(or_(*[FHIRApp.os_support.contains(str(oid)) for oid in os_support_ids]))
        filter_params['os_support'] = os_support_ids
    if fhir_support_ids:
        query = query.filter(FHIRApp.fhir_compatibility_id.in_(fhir_support_ids))
        filter_params['fhir_support'] = fhir_support_ids
    if pricing_license_ids:
        query = query.filter(FHIRApp.licensing_pricing_id.in_(pricing_license_ids))
        filter_params['pricing_license'] = pricing_license_ids
    if designed_for_ids:
        query = query.filter(FHIRApp.designed_for_id.in_(designed_for_ids))
        filter_params['designed_for'] = designed_for_ids

    apps = query.all()
    for app in apps:
        logger.debug(f"App ID: {app.id}, logo_url: {app.logo_url}")
    return render_template(
        'gallery.html',
        apps=apps,
        form=form,
        filter_params=filter_params,
        categories=Category.query.all(),
        os_supports=OSSupport.query.all(),
        fhir_supports=FHIRSupport.query.all(),
        pricing_licenses=PricingLicense.query.all(),
        designed_fors=DesignedFor.query.all()
    )

@gallery_bp.route('/gallery/<int:app_id>')
def app_detail(app_id):
    app = FHIRApp.query.get_or_404(app_id)
    logger.debug(f"App Detail ID: {app_id}, logo_url: {app.logo_url}, app_images: {app.app_images}")
    app_categories = []
    if app.categories:
        category_ids = [int(cid) for cid in app.categories.split(',') if cid]
        app_categories = Category.query.filter(Category.id.in_(category_ids)).all()

    app_os_supports = []
    if app.os_support:
        os_ids = [int(oid) for oid in app.os_support.split(',') if oid]
        app_os_supports = OSSupport.query.filter(OSSupport.id.in_(os_ids)).all()

    return render_template(
        'app_detail.html',
        app=app,
        app_categories=app_categories,
        app_os_supports=app_os_supports
    )

@gallery_bp.route('/gallery/register', methods=['GET', 'POST'])
@login_required
def register():
    form = FHIRAppForm()
    if form.validate_on_submit():
        try:
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            logger.debug(f"Ensured {UPLOAD_FOLDER} exists")
        except Exception as e:
            logger.error(f"Failed to create {UPLOAD_FOLDER}: {e}")
            flash('Error creating upload directory.', 'danger')
            return render_template('register.html', form=form)

        logo_url = form.logo_url.data
        if form.logo_upload.data:
            file = form.logo_upload.data
            if allowed_file(file.filename):
                filename = secure_filename(f"{uuid.uuid4()}_{file.filename}")
                save_path = os.path.join(UPLOAD_FOLDER, filename)
                logger.debug(f"Attempting to save logo to {save_path}")
                try:
                    file.save(save_path)
                    if os.path.exists(save_path):
                        logger.debug(f"Successfully saved logo to {save_path}")
                    else:
                        logger.error(f"Failed to save logo to {save_path}")
                        flash('Failed to save logo.', 'danger')
                        return render_template('register.html', form=form)
                    logo_url = f"/uploads/{filename}"
                    logger.debug(f"Set logo_url to {logo_url}")
                except Exception as e:
                    logger.error(f"Error saving logo to {save_path}: {e}")
                    flash('Error saving logo.', 'danger')
                    return render_template('register.html', form=form)

        app_images = []
        if form.app_image_urls.data:
            app_images.extend([url.strip() for url in form.app_image_urls.data.splitlines() if url.strip().startswith(('http://', 'https://'))])
        if form.app_image_uploads.data:
            for file in form.app_image_uploads.data:
                if file and allowed_file(file.filename):
                    filename = secure_filename(f"{uuid.uuid4()}_{file.filename}")
                    save_path = os.path.join(UPLOAD_FOLDER, filename)
                    logger.debug(f"Attempting to save app image to {save_path}")
                    try:
                        file.save(save_path)
                        if os.path.exists(save_path):
                            logger.debug(f"Successfully saved app image to {save_path}")
                        else:
                            logger.error(f"Failed to save app image to {save_path}")
                            flash('Failed to save app image.', 'danger')
                            return render_template('register.html', form=form)
                        app_images.append(f"/uploads/{filename}")
                    except Exception as e:
                        logger.error(f"Error saving app image to {save_path}: {e}")
                        flash('Error saving app image.', 'danger')
                        return render_template('register.html', form=form)

        app = FHIRApp(
            name=form.name.data,
            description=form.description.data,
            developer=form.developer.data,
            contact_email=form.contact_email.data,
            logo_url=logo_url or None,
            launch_url=form.launch_url.data,
            website=form.website.data or None,
            designed_for_id=form.designed_for.data,
            fhir_compatibility_id=form.fhir_compatibility.data,
            categories=','.join(map(str, form.categories.data)) if form.categories.data else None,
            licensing_pricing_id=form.licensing_pricing.data,
            os_support=','.join(map(str, form.os_support.data)) if form.os_support.data else None,
            app_images=','.join(app_images) if app_images else None,
            user_id=current_user.id
        )
        db.session.add(app)
        try:
            db.session.commit()
            logger.debug(f"Registered app ID: {app.id}, logo_url: {app.logo_url}, app_images: {app.app_images}")
        except Exception as e:
            logger.error(f"Error committing app to database: {e}")
            db.session.rollback()
            flash('Error saving app to database.', 'danger')
            return render_template('register.html', form=form)
        flash('App registered successfully!', 'success')
        return redirect(url_for('gallery.gallery'))
    return render_template('register.html', form=form)

@gallery_bp.route('/gallery/edit/<int:app_id>', methods=['GET', 'POST'])
@login_required
def edit_app(app_id):
    app = FHIRApp.query.get_or_404(app_id)
    if not current_user.is_admin and app.user_id != current_user.id:
        flash('You can only edit your own apps.', 'danger')
        return redirect(url_for('gallery.app_detail', app_id=app_id))

    form = FHIRAppForm(obj=app)
    if not form.is_submitted():
        if app.categories:
            form.categories.data = [int(cid) for cid in app.categories.split(',') if cid]
        if app.os_support:
            form.os_support.data = [int(oid) for oid in app.os_support.split(',') if oid]
        if app.app_images:
            current_images = [img for img in app.app_images.split(',') if img.startswith(('http://', 'https://', '/uploads/'))]
            form.app_image_urls.data = '\n'.join(current_images)
        else:
            form.app_image_urls.data = ''

    if form.validate_on_submit():
        try:
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            logger.debug(f"Ensured {UPLOAD_FOLDER} exists")
        except Exception as e:
            logger.error(f"Failed to create {UPLOAD_FOLDER}: {e}")
            flash('Error creating upload directory.', 'danger')
            return render_template('edit_app.html', form=form, app=app)

        logo_url = form.logo_url.data
        if form.logo_upload.data:
            file = form.logo_upload.data
            if allowed_file(file.filename):
                filename = secure_filename(f"{uuid.uuid4()}_{file.filename}")
                save_path = os.path.join(UPLOAD_FOLDER, filename)
                logger.debug(f"Attempting to save updated logo to {save_path}")
                try:
                    file.save(save_path)
                    if os.path.exists(save_path):
                        logger.debug(f"Successfully saved updated logo to {save_path}")
                    else:
                        logger.error(f"Failed to save updated logo to {save_path}")
                        flash('Failed to save logo.', 'danger')
                        return render_template('edit_app.html', form=form, app=app)
                    logo_url = f"/uploads/{filename}"
                    logger.debug(f"Set logo_url to {logo_url}")
                except Exception as e:
                    logger.error(f"Error saving updated logo to {save_path}: {e}")
                    flash('Error saving logo.', 'danger')
                    return render_template('edit_app.html', form=form, app=app)
        elif not logo_url:
            logo_url = app.logo_url

        app_images = [url.strip() for url in form.app_image_urls.data.splitlines() if url.strip()]
        if form.app_image_uploads.data:
            for file in form.app_image_uploads.data:
                if file and allowed_file(file.filename):
                    filename = secure_filename(f"{uuid.uuid4()}_{file.filename}")
                    save_path = os.path.join(UPLOAD_FOLDER, filename)
                    logger.debug(f"Attempting to save updated app image to {save_path}")
                    try:
                        file.save(save_path)
                        if os.path.exists(save_path):
                            logger.debug(f"Successfully saved updated app image to {save_path}")
                        else:
                            logger.error(f"Failed to save updated app image to {save_path}")
                            flash('Failed to save app image.', 'danger')
                            return render_template('edit_app.html', form=form, app=app)
                        app_images.append(f"/uploads/{filename}")
                    except Exception as e:
                        logger.error(f"Error saving updated app image to {save_path}: {e}")
                        flash('Error saving app image.', 'danger')
                        return render_template('edit_app.html', form=form, app=app)

        app.name = form.name.data
        app.description = form.description.data
        app.developer = form.developer.data
        app.contact_email = form.contact_email.data
        app.logo_url = logo_url
        app.launch_url = form.launch_url.data
        app.website = form.website.data or None
        app.designed_for_id = form.designed_for.data
        app.fhir_compatibility_id = form.fhir_compatibility.data
        app.categories = ','.join(map(str, form.categories.data)) if form.categories.data else None
        app.licensing_pricing_id = form.licensing_pricing.data
        app.os_support = ','.join(map(str, form.os_support.data)) if form.os_support.data else None
        app.app_images = ','.join(app_images) if app_images else None
        try:
            db.session.commit()
            logger.debug(f"Updated app ID: {app.id}, logo_url: {app.logo_url}, app_images: {app.app_images}")
        except Exception as e:
            logger.error(f"Error committing app update to database: {e}")
            db.session.rollback()
            flash('Error updating app in database.', 'danger')
            return render_template('edit_app.html', form=form, app=app)
        flash('App updated successfully!', 'success')
        return redirect(url_for('gallery.app_detail', app_id=app_id))

    return render_template('edit_app.html', form=form, app=app)

@gallery_bp.route('/gallery/delete/<int:app_id>', methods=['POST'])
@login_required
def delete_app(app_id):
    app = FHIRApp.query.get_or_404(app_id)
    if not current_user.is_admin and app.user_id != current_user.id:
        flash('You can only delete your own apps.', 'danger')
        return redirect(url_for('gallery.app_detail', app_id=app_id))
    db.session.delete(app)
    db.session.commit()
    flash(f'App "{app.name}" deleted successfully.', 'success')
    return redirect(url_for('gallery.gallery'))

@gallery_bp.route('/my-listings')
@login_required
def my_listings():
    apps = FHIRApp.query.filter_by(user_id=current_user.id).all()
    return render_template('my_listings.html', apps=apps)

@gallery_bp.route('/admin/categories', methods=['GET', 'POST'])
@login_required
def manage_categories():
    if not current_user.is_admin:
        flash('Admin access required.', 'danger')
        return redirect(url_for('gallery.landing'))
    form = CategoryForm()
    if form.validate_on_submit():
        category = Category(name=form.name.data)
        db.session.add(category)
        db.session.commit()
        flash('Category added successfully!', 'success')
        return redirect(url_for('gallery.manage_categories'))
    categories = Category.query.all()
    return render_template('admin_categories.html', form=form, categories=categories)

@gallery_bp.route('/admin/categories/delete/<int:category_id>', methods=['POST'])
@login_required
def delete_category(category_id):
    if not current_user.is_admin:
        flash('Admin access required.', 'danger')
        return redirect(url_for('gallery.landing'))
    category = Category.query.get_or_404(category_id)
    db.session.delete(category)
    db.session.commit()
    flash(f'Category "{category.name}" deleted successfully.', 'success')
    return redirect(url_for('gallery.manage_categories'))

@gallery_bp.route('/admin/apps')
@login_required
def admin_apps():
    if not current_user.is_admin:
        flash('Admin access required.', 'danger')
        return redirect(url_for('gallery.landing'))
    apps = FHIRApp.query.all()
    return render_template('admin_apps.html', apps=apps)

@gallery_bp.route('/admin/users', methods=['GET'])
@login_required
def admin_users():
    if not current_user.is_admin:
        flash('Admin access required.', 'danger')
        return redirect(url_for('gallery.landing'))
    users = User.query.all()
    return render_template('admin_users.html', users=users)

@gallery_bp.route('/admin/users/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    if not current_user.is_admin:
        flash('Admin access required.', 'danger')
        return redirect(url_for('gallery.landing'))
    user = User.query.get_or_404(user_id)
    form = UserEditForm(user=user, obj=user)
    if form.validate_on_submit():
        user.username = form.username.data
        user.email = form.email.data
        user.is_admin = form.is_admin.data
        user.force_password_change = form.force_password_change.data
        if form.reset_password.data:
            user.set_password(form.reset_password.data)
        try:
            db.session.commit()
            flash(f'User "{user.username}" updated successfully.', 'success')
        except Exception as e:
            logger.error(f"Error updating user: {e}")
            db.session.rollback()
            flash('Error updating user.', 'danger')
        return redirect(url_for('gallery.admin_users'))
    return render_template('admin_edit_user.html', form=form, user=user)