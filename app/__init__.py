# app/__init__.py

import datetime
import os
import importlib
import logging
from flask import Flask, render_template, Blueprint, current_app
from config import Config
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager

# Instantiate Extensions
db = SQLAlchemy()
migrate = Migrate()

def create_app(config_class='config.DevelopmentConfig'):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)

    from app.fhir_ig_importer import bp as fhir_ig_importer_bp
    app.register_blueprint(fhir_ig_importer_bp)

    @app.route('/')
    def index():
        return render_template('index.html')

    return app

from app import models