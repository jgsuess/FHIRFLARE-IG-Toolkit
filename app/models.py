# app/models.py
from app import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import json

# --- ProcessedIg Model (MODIFIED for Examples) ---
class ProcessedIg(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    package_name = db.Column(db.String(150), nullable=False, index=True)
    package_version = db.Column(db.String(50), nullable=False, index=True)
    processed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    status = db.Column(db.String(50), default='processed', nullable=True)
    # Stores list of dicts: [{'name': 'Type', 'must_support': bool}, ...]
    resource_types_info_json = db.Column(db.Text, nullable=True)
    # Stores dict: {'TypeName': ['path1', 'path2'], ...}
    must_support_elements_json = db.Column(db.Text, nullable=True)
    # --- ADDED: Store example files found, grouped by type ---
    # Structure: {'TypeName': ['example1.json', 'example1.xml'], ...}
    examples_json = db.Column(db.Text, nullable=True)
    # --- End Add ---

    __table_args__ = (db.UniqueConstraint('package_name', 'package_version', name='uq_processed_ig_name_version'),)

    # Property for resource_types_info
    @property
    def resource_types_info(self):
        # ... (getter as before) ...
        if self.resource_types_info_json:
            try: return json.loads(self.resource_types_info_json)
            except json.JSONDecodeError: return []
        return []

    @resource_types_info.setter
    def resource_types_info(self, types_info_list):
         # ... (setter as before) ...
        if types_info_list and isinstance(types_info_list, list):
            sorted_list = sorted(types_info_list, key=lambda x: x.get('name', ''))
            self.resource_types_info_json = json.dumps(sorted_list)
        else: self.resource_types_info_json = None

    # Property for must_support_elements
    @property
    def must_support_elements(self):
        # ... (getter as before) ...
        if self.must_support_elements_json:
            try: return json.loads(self.must_support_elements_json)
            except json.JSONDecodeError: return {}
        return {}

    @must_support_elements.setter
    def must_support_elements(self, ms_elements_dict):
        # ... (setter as before) ...
         if ms_elements_dict and isinstance(ms_elements_dict, dict):
            self.must_support_elements_json = json.dumps(ms_elements_dict)
         else: self.must_support_elements_json = None

    # --- ADDED: Property for examples ---
    @property
    def examples(self):
        """Returns the stored example filenames as a Python dict."""
        if self.examples_json:
            try:
                # Return dict {'TypeName': ['file1.json', 'file2.xml'], ...}
                return json.loads(self.examples_json)
            except json.JSONDecodeError:
                return {} # Return empty dict on parse error
        return {}

    @examples.setter
    def examples(self, examples_dict):
        """Stores a Python dict of example filenames as a JSON string."""
        if examples_dict and isinstance(examples_dict, dict):
            # Sort filenames within each list? Optional.
            # for key in examples_dict: examples_dict[key].sort()
            self.examples_json = json.dumps(examples_dict)
        else:
            self.examples_json = None
    # --- End Add ---

    def __repr__(self):
        count = len(self.resource_types_info)
        ms_count = sum(1 for item in self.resource_types_info if item.get('must_support'))
        ex_count = sum(len(v) for v in self.examples.values()) # Count total example files
        return f"<ProcessedIg {self.package_name}#{self.package_version} ({self.status}, {count} types, {ms_count} MS, {ex_count} examples)>"
