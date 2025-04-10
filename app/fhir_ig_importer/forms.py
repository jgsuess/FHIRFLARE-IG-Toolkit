# app/modules/fhir_ig_importer/forms.py

from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Regexp

class IgImportForm(FlaskForm):
    """Form for specifying an IG package to import."""
    # Basic validation for FHIR package names (e.g., hl7.fhir.r4.core)
    package_name = StringField('Package Name (e.g., hl7.fhir.au.base)', validators=[
        DataRequired(),
        Regexp(r'^[a-zA-Z0-9]+(\.[a-zA-Z0-9]+)+$', message='Invalid package name format.')
    ])
    # Basic validation for version (e.g., 4.1.0, current)
    package_version = StringField('Package Version (e.g., 4.1.0 or current)', validators=[
        DataRequired(),
        Regexp(r'^[a-zA-Z0-9\.\-]+$', message='Invalid version format.')
    ])
    submit = SubmitField('Fetch & Download IG')