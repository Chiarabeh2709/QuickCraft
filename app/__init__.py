import re

from flask import Flask, render_template
from flask_login import current_user
from sqlalchemy.exc import OperationalError

from app.blueprints import admin, auth, bookings, jobs, main, profiles, projects, quotes, reviews
from app.cli import reset_db
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

    for module in (main, auth, jobs, quotes, bookings, projects, profiles, reviews, admin):
        app.register_blueprint(module.bp)

    @app.template_filter("date_format")
    def date_format(value):
        return value.strftime("%d %b %Y") if value else ""

    @app.template_filter("slugify")
    def slugify(value):
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")

    app.register_error_handler(403, forbidden)
    app.register_error_handler(404, not_found)
    app.cli.add_command(reset_db)

    with app.app_context():
        # Imported here so SQLAlchemy knows every table before creating them.
        from app import models

        db.create_all()
        try:
            models.ensure_admin(app.config["ADMIN_EMAIL"], app.config["ADMIN_PASSWORD"])
        except OperationalError:
            # An outdated table must not stop startup, or `flask reset-db` could never run to fix it.
            db.session.rollback()
            app.logger.warning("Could not check the admin: the database is outdated. Locally, run: flask reset-db")

    return app


def forbidden(_error):
    if not current_user.is_authenticated:
        message = "You do not have permission to view this page."
    elif current_user.role == "admin":
        message = "This page is not available to administrators."
    elif current_user.role == "customer":
        message = "This page is only available to the relevant customer or tradespeople."
    elif current_user.verification_status == "pending":
        message = "Your account is waiting for approval before you can access this page."
    elif current_user.is_verified_tradesperson:
        message = "This page is only available to the relevant tradesperson."
    else:
        message = "You do not have permission to view this page."
    return render_template("403.html", message=message), 403


def not_found(_error):
    return render_template("404.html"), 404
