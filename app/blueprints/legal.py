# Terms of Use: the public text, and acceptance for accounts that have not accepted the current version.
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.auth import landing_page
from app.extensions import db
from app.models import ROLE_ADMIN, TERMS_VERSION

bp = Blueprint("legal", __name__)

# What a logged-in person can still reach without accepting: the text itself, the acceptance, and leaving.
OPEN_ENDPOINTS = {"legal.terms", "legal.accept", "auth.logout", "static", None}


@bp.before_app_request
def require_current_terms():
    # Runs before every request, so no page can be reached around the acceptance. The admin is not a customer.
    if not current_user.is_authenticated or current_user.role == ROLE_ADMIN:
        return None
    if current_user.has_accepted_current_terms or request.endpoint in OPEN_ENDPOINTS:
        return None
    return redirect(url_for("legal.accept"))


@bp.route("/terms")
def terms():
    return render_template("legal/terms.html", version=TERMS_VERSION)


@bp.route("/terms/accept", methods=["GET", "POST"])
@login_required
def accept():
    if current_user.has_accepted_current_terms:
        return redirect(landing_page(current_user))

    if request.method == "POST":
        if request.form.get("accept_terms"):
            current_user.accept_terms()
            db.session.commit()
            flash("Thank you. You accepted the Terms of Use.", "success")
            return redirect(landing_page(current_user))
        flash("Please accept the Terms of Use to continue.", "danger")

    return render_template("legal/accept.html", version=TERMS_VERSION)
