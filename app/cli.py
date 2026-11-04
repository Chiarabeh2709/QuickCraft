import os

import click
from flask import current_app
from flask.cli import with_appcontext

from app.extensions import db
from app.models import ensure_admin


@click.command("reset-db")
@with_appcontext
def reset_db():
    """Local development only: delete all data and start with fresh tables."""
    # Checked first, and for any value: Render sets RENDER, and this must never wipe the live database.
    if "RENDER" in os.environ:
        raise click.ClickException("Refusing to run: the RENDER environment variable is set. Nothing was changed.")

    db.drop_all()
    db.create_all()

    email = current_app.config["ADMIN_EMAIL"]
    password = current_app.config["ADMIN_PASSWORD"]
    ensure_admin(email, password)

    # >>> SEED DATA: call the seed script here once it exists (feature 7). <<<

    click.echo(f"Database reset: {db.engine.url}")
    if email and password:
        click.echo(f"Admin created: {email}")
    else:
        click.echo("No admin created: ADMIN_EMAIL and ADMIN_PASSWORD are not both set.")
