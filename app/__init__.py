from flask import Flask

from app.blueprints import admin, auth, bookings, jobs, main, projects, quotes, reviews
from app.extensions import db, login_manager
from config import Config


def create_app(config_class=Config):
    # A factory lets tests build an app with their own config and database.
    app = Flask(__name__)
    app.config.from_object(config_class)

    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY is not set. Copy .env.example to .env and fill it in.")

    db.init_app(app)
    login_manager.init_app(app)

    for module in (main, auth, jobs, quotes, bookings, projects, reviews, admin):
        app.register_blueprint(module.bp)

    with app.app_context():
        # Imported here so SQLAlchemy knows every table before creating them.
        from app import models  # noqa: F401

        db.create_all()

    return app
