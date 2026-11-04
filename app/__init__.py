from flask import Flask, render_template

from app.blueprints import admin, auth, bookings, jobs, main, projects, quotes, reviews
from app.extensions import csrf, db, login_manager
from config import Config


def create_app(config_class=Config):
    # A factory lets tests build an app with their own config and database.
    app = Flask(__name__)
    app.config.from_object(config_class)

    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY is not set. Copy .env.example to .env and fill it in.")

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    for module in (main, auth, jobs, quotes, bookings, projects, reviews, admin):
        app.register_blueprint(module.bp)

    app.register_error_handler(403, forbidden)

    with app.app_context():
        # Imported here so SQLAlchemy knows every table before creating them.
        from app import models

        db.create_all()
        models.ensure_admin(app.config["ADMIN_EMAIL"], app.config["ADMIN_PASSWORD"])

    return app


def forbidden(_error):
    return render_template("403.html"), 403
