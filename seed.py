from app import db, create_app
from app.models import Category, OSSupport, FHIRSupport, PricingLicense, DesignedFor, User

app = create_app()

def seed_table(model, names):
    for name in names:
        if not model.query.filter_by(name=name).first():
            db.session.add(model(name=name))
    db.session.commit()

def seed_admin_user():
    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            email='admin@fhirpad.com',
            is_admin=True,
            force_password_change=True
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print("Default admin user created: username=admin, password=admin123")

with app.app_context():
    db.create_all()

    try:
        # Seed admin user
        seed_admin_user()

        # Seed categories
        categories = [
            'Care Coordination', 'Clinical Research', 'Data Visualization', ' Disease Management',
            'Genomics', 'Medication Management', 'Patient Engagement', 'Population Health',
            'Risk Calculation', 'FHIR Tools', 'Telehealth'
        ]
        seed_table(Category, categories)

        # Seed OS support
        os_supports = ['iOS', 'Android', 'Web', 'Mac', 'Windows', 'Linux']
        seed_table(OSSupport, os_supports)

        # Seed FHIR support
        fhir_supports = ['DSTU 2', 'STU 3', 'R4', 'R5']
        seed_table(FHIRSupport, fhir_supports)

        # Seed pricing/licenses
        pricings = ['Open Source', 'Free', 'Per User', 'Site-Based', 'Subscription']
        seed_table(PricingLicense, pricings)

        # Seed designed for
        designed_fors = ['Clinicians', 'Patients', 'Patients & Clinicians', 'IT Professionals']
        seed_table(DesignedFor, designed_fors)

        print("Database seeded successfully!")
    except Exception as e:
        print(f"Seeding failed: {str(e)}")
        db.session.rollback()