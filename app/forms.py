from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed, MultipleFileField
from wtforms import StringField, TextAreaField, SubmitField, PasswordField, SelectField, SelectMultipleField, BooleanField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Optional, ValidationError
import re
from app.models import Category, OSSupport, FHIRSupport, PricingLicense, DesignedFor, User
from app import db

def validate_url_or_path(form, field):
    if not field.data:
        return
    # Allow upload paths like /uploads/<uuid>_<filename>.jpg|png
    if field.data.startswith('/uploads/'):
        path_pattern = r'^/uploads/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_[\w\-\.]+\.(jpg|png)$'
        if re.match(path_pattern, field.data, re.I):
            return
    # Allow external URLs
    url_pattern = r'^(https?:\/\/)?([\w\-]+\.)+[\w\-]+(\/[\w\-\.]*)*\/?(\?[^\s]*)?(#[^\s]*)?$'
    if not re.match(url_pattern, field.data):
        raise ValidationError('Invalid URL or file path.')

def validate_username(form, field):
    if form.user and field.data != form.user.username:  # Check if username changed
        existing_user = User.query.filter_by(username=field.data).first()
        if existing_user:
            raise ValidationError('Username already taken.')

def validate_email(form, field):
    if form.user and field.data != form.user.email:  # Check if email changed
        existing_user = User.query.filter_by(email=field.data).first()
        if existing_user:
            raise ValidationError('Email already registered.')

class FHIRAppForm(FlaskForm):
    name = StringField('App Name', validators=[DataRequired(), Length(min=3, max=100)])
    description = TextAreaField('Description', validators=[DataRequired(), Length(min=10, max=500)])
    developer = StringField('Developer/Organization', validators=[DataRequired(), Length(min=3, max=100)])
    contact_email = StringField('Contact Email', validators=[DataRequired(), Email()])
    logo_url = StringField('Logo URL', validators=[Optional(), validate_url_or_path], render_kw={"placeholder": "https://example.com/logo.png or leave blank to upload"})
    logo_upload = FileField('Upload Logo', validators=[FileAllowed(['jpg', 'png'], 'Images only!')])
    launch_url = StringField('Launch URL', validators=[DataRequired(), Length(max=200)])
    website = StringField('Company Website', validators=[Optional(), Length(max=200)], render_kw={"placeholder": "https://example.com"})
    designed_for = SelectField('Designed For', coerce=int, validators=[DataRequired()])
    fhir_compatibility = SelectField('FHIR Compatibility', coerce=int, validators=[DataRequired()])
    categories = SelectMultipleField('Categories', coerce=int, validators=[DataRequired()])
    licensing_pricing = SelectField('Licensing & Pricing', coerce=int, validators=[DataRequired()])
    os_support = SelectMultipleField('OS Support', coerce=int, validators=[DataRequired()])
    app_image_urls = TextAreaField('App Image URLs (one per line)', validators=[Optional(), Length(max=1000)], render_kw={"placeholder": "e.g., https://example.com/image1.png"})
    app_image_uploads = MultipleFileField('Upload App Images', validators=[FileAllowed(['jpg', 'png'], 'Images only!')])
    submit = SubmitField('Register App')

    def __init__(self, *args, **kwargs):
        super(FHIRAppForm, self).__init__(*args, **kwargs)
        self.categories.choices = [(c.id, c.name) for c in Category.query.all()]
        self.os_support.choices = [(o.id, o.name) for o in OSSupport.query.all()]
        self.fhir_compatibility.choices = [(f.id, f.name) for f in FHIRSupport.query.all()]
        self.licensing_pricing.choices = [(p.id, p.name) for p in PricingLicense.query.all()]
        self.designed_for.choices = [(d.id, d.name) for d in DesignedFor.query.all()]

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Log In')

class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Register')

class GalleryFilterForm(FlaskForm):
    categories = StringField('Categories', validators=[Length(max=500)], render_kw={"placeholder": "e.g., Clinical, Billing"})
    fhir_compatibility = StringField('FHIR Compatibility', validators=[Length(max=200)], render_kw={"placeholder": "e.g., R4, US Core"})
    submit = SubmitField('Filter')

class CategoryForm(FlaskForm):
    name = StringField('Category Name', validators=[DataRequired(), Length(min=3, max=100)])
    submit = SubmitField('Save Category')

class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password')])
    submit = SubmitField('Change Password')

class UserEditForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80), validate_username])
    email = StringField('Email', validators=[DataRequired(), Email(), validate_email])
    is_admin = BooleanField('Admin Status')
    force_password_change = BooleanField('Force Password Change')
    reset_password = PasswordField('Reset Password', validators=[Optional(), Length(min=6)])
    confirm_reset_password = PasswordField('Confirm Reset Password', validators=[Optional(), EqualTo('reset_password')])
    submit = SubmitField('Save Changes')

    def __init__(self, user=None, *args, **kwargs):
        super(UserEditForm, self).__init__(*args, **kwargs)
        self.user = user  # Store the user object for validation