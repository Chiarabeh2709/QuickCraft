# Feature 1: registration, login and logout.
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db
from app.models import (
    ROLE_ADMIN,
    ROLE_CUSTOMER,
    ROLE_TRADESPERSON,
    TRADES,
    VERIFICATION_PENDING,
    TradespersonProfile,
    TradespersonTrade,
    User,
    find_user_by_email,
    normalise_email,
)

bp = Blueprint("auth", __name__, url_prefix="/auth")

MIN_PASSWORD_LENGTH = 8


def form_value(name):
    return request.form.get(name, "").strip()


def account_errors():
    errors = []
    if not form_value("name"):
        errors.append("Please enter your name.")
    if "@" not in form_value("email"):
        errors.append("Please enter a valid email address.")
    elif find_user_by_email(form_value("email")):
        errors.append("An account with this email already exists.")
    # Not stripped: spaces are allowed in a password.
    if len(request.form.get("password", "")) < MIN_PASSWORD_LENGTH:
        errors.append(f"Your password needs at least {MIN_PASSWORD_LENGTH} characters.")
    return errors


def tradesperson_errors():
    errors = []
    if not form_value("company_name"):
        errors.append("Please enter your company name.")
    if form_value("trade") not in TRADES:
        errors.append("Please choose your trade.")
    if not form_value("service_area"):
        errors.append("Please enter your city or service area.")
    if not form_value("qualification_reference"):
        errors.append("Please enter your qualification reference number.")
    return errors


def build_user(role, verification_status=None):
    # The role is set by the route, never read from the form, so nobody can sign up as admin.
    user = User(
        name=form_value("name"),
        email=normalise_email(form_value("email")),
        role=role,
        verification_status=verification_status,
    )
    user.set_password(request.form.get("password", ""))
    return user


def show_errors(errors):
    for error in errors:
        flash(error, "danger")


def landing_page(user):
    if user.role == ROLE_ADMIN:
        return url_for("admin.pending_tradespeople")
    # Anyone who left before confirming gets the page again at their next login.
    if not user.email_verified:
        return url_for("auth.confirm_email")
    return url_for("main.home")


@bp.route("/signup/customer", methods=["GET", "POST"])
def signup_customer():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))

    if request.method == "POST":
        errors = account_errors()
        if not errors:
            user = build_user(ROLE_CUSTOMER)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("Welcome to QuickCraft!", "success")
            return redirect(url_for("auth.confirm_email"))
        show_errors(errors)

    return render_template("auth/signup_customer.html")


@bp.route("/signup/tradesperson", methods=["GET", "POST"])
def signup_tradesperson():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))

    if request.method == "POST":
        errors = account_errors() + tradesperson_errors()
        if not errors:
            user = build_user(ROLE_TRADESPERSON, VERIFICATION_PENDING)
            user.profile = TradespersonProfile(
                company_name=form_value("company_name"),
                service_area=form_value("service_area"),
                qualification_reference=form_value("qualification_reference"),
                trades=[TradespersonTrade(trade=form_value("trade"))],
            )
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("auth.confirm_email"))
        show_errors(errors)

    return render_template("auth/signup_tradesperson.html", trades=TRADES)


@bp.route("/confirm-email", methods=["GET", "POST"])
@login_required
def confirm_email():
    if current_user.email_verified:
        return redirect(url_for("main.home"))

    if request.method == "POST":
        # Simulated: no email is sent. There is no user id in the URL, so people can only confirm themselves.
        current_user.email_verified = True
        db.session.commit()
        flash("Your email is confirmed.", "success")
        return redirect(url_for("main.home"))

    return render_template("auth/confirm_email.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(landing_page(current_user))

    if request.method == "POST":
        user = find_user_by_email(form_value("email"))
        if user and user.check_password(request.form.get("password", "")):
            login_user(user)
            # We ignore Flask-Login's ?next= value, because an unchecked one could redirect to another site.
            return redirect(landing_page(user))
        # The same message for both cases, so nobody can test which emails have accounts.
        flash("Wrong email or password.", "danger")

    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.home"))
