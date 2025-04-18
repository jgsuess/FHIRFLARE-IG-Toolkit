# forms.py
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, TextAreaField, BooleanField, SubmitField, FileField
from wtforms.validators import DataRequired, Regexp, ValidationError, Optional
import json
import xml.etree.ElementTree as ET
import re

# Existing forms (IgImportForm, ValidationForm) remain unchanged
class IgImportForm(FlaskForm):
    package_name = StringField('Package Name', validators=[
        DataRequired(),
        Regexp(r'^[a-zA-Z0-9][a-zA-Z0-9\-\.]*[a-zA-Z0-9]$', message="Invalid package name format.")
    ], render_kw={'placeholder': 'e.g., hl7.fhir.au.core'})
    package_version = StringField('Package Version', validators=[
        DataRequired(),
        Regexp(r'^[a-zA-Z0-9\.\-]+$', message="Invalid version format. Use alphanumeric characters, dots, or hyphens (e.g., 1.2.3, 1.1.0-preview, current).")
    ], render_kw={'placeholder': 'e.g., 1.1.0-preview'})
    dependency_mode = SelectField('Dependency Mode', choices=[
        ('recursive', 'Current Recursive'),
        ('patch-canonical', 'Patch Canonical Versions'),
        ('tree-shaking', 'Tree Shaking (Only Used Dependencies)')
    ], default='recursive')
    submit = SubmitField('Import')

class ValidationForm(FlaskForm):
    package_name = StringField('Package Name', validators=[DataRequired()])
    version = StringField('Package Version', validators=[DataRequired()])
    include_dependencies = BooleanField('Include Dependencies', default=True)
    mode = SelectField('Validation Mode', choices=[
        ('single', 'Single Resource'),
        ('bundle', 'Bundle')
    ], default='single')
    sample_input = TextAreaField('Sample Input', validators=[
        DataRequired(),
        lambda form, field: validate_json(field.data, form.mode.data)
    ])
    submit = SubmitField('Validate')

def validate_json(data, mode):
    """Custom validator to ensure input is valid JSON and matches the selected mode."""
    try:
        parsed = json.loads(data)
        if mode == 'single' and not isinstance(parsed, dict):
            raise ValueError("Single resource mode requires a JSON object.")
        if mode == 'bundle' and (not isinstance(parsed, dict) or parsed.get('resourceType') != 'Bundle'):
            raise ValueError("Bundle mode requires a JSON object with resourceType 'Bundle'.")
    except json.JSONDecodeError:
        raise ValidationError("Invalid JSON format.")
    except ValueError as e:
        raise ValidationError(str(e))

class FSHConverterForm(FlaskForm):
    package = SelectField('FHIR Package (Optional)', choices=[('', 'None')], validators=[Optional()])
    input_mode = SelectField('Input Mode', choices=[
        ('file', 'Upload File'),
        ('text', 'Paste Text')
    ], validators=[DataRequired()])
    fhir_file = FileField('FHIR Resource File (JSON/XML)', validators=[Optional()])
    fhir_text = TextAreaField('FHIR Resource Text (JSON/XML)', validators=[Optional()])
    output_style = SelectField('Output Style', choices=[
        ('file-per-definition', 'File per Definition'),
        ('group-by-fsh-type', 'Group by FSH Type'),
        ('group-by-profile', 'Group by Profile'),
        ('single-file', 'Single File')
    ], validators=[DataRequired()])
    log_level = SelectField('Log Level', choices=[
        ('error', 'Error'),
        ('warn', 'Warn'),
        ('info', 'Info'),
        ('debug', 'Debug')
    ], validators=[DataRequired()])
    fhir_version = SelectField('FXML Version', choices=[
        ('', 'Auto-detect'),
        ('4.0.1', 'R4'),
        ('4.3.0', 'R4B'),
        ('5.0.0', 'R5')
    ], validators=[Optional()])
    fishing_trip = BooleanField('Run Fishing Trip (Round-Trip Validation with SUSHI)', default=False)
    dependencies = TextAreaField('Dependencies (e.g., hl7.fhir.us.core@6.1.0)', validators=[Optional()])
    indent_rules = BooleanField('Indent Rules with Context Paths', default=False)
    meta_profile = SelectField('Meta Profile Handling', choices=[
        ('only-one', 'Only One Profile (Default)'),
        ('first', 'First Profile'),
        ('none', 'Ignore Profiles')
    ], validators=[DataRequired()])
    alias_file = FileField('Alias FSH File', validators=[Optional()])
    no_alias = BooleanField('Disable Alias Generation', default=False)
    submit = SubmitField('Convert to FSH')

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False
        if self.input_mode.data == 'file' and not self.fhir_file.data:
            self.fhir_file.errors.append('File is required when input mode is Upload File.')
            return False
        if self.input_mode.data == 'text' and not self.fhir_text.data:
            self.fhir_text.errors.append('Text input is required when input mode is Paste Text.')
            return False
        if self.input_mode.data == 'text' and self.fhir_text.data:
            try:
                content = self.fhir_text.data.strip()
                if content.startswith('{'):
                    json.loads(content)
                elif content.startswith('<'):
                    ET.fromstring(content)
                else:
                    self.fhir_text.errors.append('Text input must be valid JSON or XML.')
                    return False
            except (json.JSONDecodeError, ET.ParseError):
                self.fhir_text.errors.append('Invalid JSON or XML format.')
                return False
        if self.dependencies.data:
            for dep in self.dependencies.data.split('\n'):
                dep = dep.strip()
                if dep and not re.match(r'^[a-zA-Z0-9\-\.]+@[a-zA-Z0-9\.\-]+$', dep):
                    self.dependencies.errors.append(f'Invalid dependency format: {dep}. Use package@version (e.g., hl7.fhir.us.core@6.1.0).')
                    return False
        if self.alias_file.data:
            content = self.alias_file.data.read().decode('utf-8')
            if not content.strip().endswith('.fsh'):
                self.alias_file.errors.append('Alias file must be a valid FSH file (.fsh).')
                return False
        return True