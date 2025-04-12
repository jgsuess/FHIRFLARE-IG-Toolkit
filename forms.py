from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, TextAreaField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Regexp, Optional

class IgImportForm(FlaskForm):
    package_name = StringField('Package Name', validators=[
        DataRequired(),
        Regexp(r'^[a-zA-Z0-9][a-zA-Z0-9\-\.]*[a-zA-Z0-9]$', message="Invalid package name format.")
    ])
    package_version = StringField('Package Version', validators=[
        DataRequired(),
        Regexp(r'^[a-zA-Z0-9\.\-]+$', message="Invalid version format. Use alphanumeric characters, dots, or hyphens (e.g., 1.2.3, 1.1.0-preview, current).")
    ])
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
    sample_input = TextAreaField('Sample Input', validators=[DataRequired()])
    submit = SubmitField('Validate')

def validate_json(field, mode):
    """Custom validator to ensure input is valid JSON and matches the selected mode."""
    import json
    try:
        data = json.loads(field)
        if mode == 'single' and not isinstance(data, dict):
            raise ValueError("Single resource mode requires a JSON object.")
        if mode == 'bundle' and (not isinstance(data, dict) or data.get('resourceType') != 'Bundle'):
            raise ValueError("Bundle mode requires a JSON object with resourceType 'Bundle'.")
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON format.")
    except ValueError as e:
        raise ValueError(str(e))