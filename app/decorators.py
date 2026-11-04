from functools import wraps

from flask import abort, flash, redirect, url_for
from flask_login import current_user, login_required

from app.models import ROLE_ADMIN, ROLE_CUSTOMER


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.role != ROLE_ADMIN:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def confirmed_customer_required(view):
    # For posting jobs. An unconfirmed customer can fix this in one click, so they get the page, not an error.
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.role != ROLE_CUSTOMER:
            abort(403)
        if not current_user.email_verified:
            flash("Please confirm your email before posting a job.", "info")
            return redirect(url_for("auth.confirm_email"))
        return view(*args, **kwargs)

    return wrapper


def verified_tradesperson_required(view):
    # Pending and rejected tradespeople are logged in but must not see jobs or send quotes.
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_verified_tradesperson:
            abort(403)
        return view(*args, **kwargs)

    return wrapper
