# Feature 1: registration, login and logout.
import re

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
FIX_FIELDS_MESSAGE = "Please correct the fields marked below."
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PHONE_PATTERN = re.compile(r"\+?[0-9 ]+")
# International numbers have at most 15 digits; below 6 it is certainly not a real number.
MIN_PHONE_DIGITS = 6
MAX_PHONE_DIGITS = 15
MAX_PHONE_LENGTH = 30

# Field, message if empty, maximum length. The inputs have maxlength, but a hand-made request does not.
ACCOUNT_TEXT_FIELDS = (
    ("name", "Please enter your full name.", 120),
    ("street", "Please enter your street and house number.", 120),
    ("postal_code", "Please enter your postal code.", 10),
    ("city", "Please enter your city.", 120),
)
TRADESPERSON_TEXT_FIELDS = (
    ("company_name", "Please enter your company name.", 120),
    ("service_area", "Please enter your city or service area.", 120),
    ("qualification_reference", "Please enter your qualification reference number.", 50),
)


def form_value(name):
    return request.form.get(name, "").strip()


def text_errors(fields):
    errors = {}
    for name, empty_message, max_length in fields:
        if not form_value(name):
            errors[name] = empty_message
        elif len(form_value(name)) > max_length:
            errors[name] = f"This can have at most {max_length} characters."
    return errors


def email_error():
    email = form_value("email")
    if len(email) > 255 or not EMAIL_PATTERN.fullmatch(email):
        return "Please enter a valid email address, like name@example.com."
    if find_user_by_email(email):
        return "An account with this email already exists."
    return None


def phone_is_valid(phone):
    if len(phone) > MAX_PHONE_LENGTH or not PHONE_PATTERN.fullmatch(phone):
        return False
    digits = phone.replace("+", "").replace(" ", "")
    return MIN_PHONE_DIGITS <= len(digits) <= MAX_PHONE_DIGITS


def account_errors():
    """Returns {field name: message}, so each message can be shown next to its field."""
    errors = text_errors(ACCOUNT_TEXT_FIELDS)
    email_message = email_error()
    if email_message:
        errors["email"] = email_message
    if not phone_is_valid(form_value("phone")):
        errors["phone"] = "Please enter a valid phone number, using only digits, spaces and an optional + at the start."
    # Not stripped: spaces are allowed in a password.
    if len(request.form.get("password", "")) < MIN_PASSWORD_LENGTH:
        errors["password"] = f"Your password needs at least {MIN_PASSWORD_LENGTH} characters."
    # The browser requires the box too, but a hand-made request does not.
    if not request.form.get("accept_terms"):
        errors["accept_terms"] = "Please accept the Terms of Use to create an account."
    return errors


def tradesperson_errors():
    errors = text_errors(TRADESPERSON_TEXT_FIELDS)
    if form_value("trade") not in TRADES:
        errors["trade"] = "Please choose your trade."
    return errors


def build_user(role, verification_status=None):
    # The role is set by the route, never read from the form, so nobody can sign up as admin.
    user = User(
        name=form_value("name"),
        email=normalise_email(form_value("email")),
        phone=form_value("phone"),
        # One column: the address is only ever displayed as a whole, never searched by its parts.
        address=f"{form_value('street')}, {form_value('postal_code')} {form_value('city')}",
        role=role,
        verification_status=verification_status,
    )
    user.set_password(request.form.get("password", ""))
    user.accept_terms()  # Checked in account_errors; stored as the proof of consent.
    return user


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

    errors = {}
    if request.method == "POST":
        errors = account_errors()
        if not errors:
            user = build_user(ROLE_CUSTOMER)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("Welcome to QuickCraft!", "success")
            return redirect(url_for("auth.confirm_email"))
        flash(FIX_FIELDS_MESSAGE, "danger")

    return render_template("auth/signup_customer.html", errors=errors)


@bp.route("/signup/tradesperson", methods=["GET", "POST"])
def signup_tradesperson():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))

    errors = {}
    if request.method == "POST":
        errors = account_errors() | tradesperson_errors()
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
        flash(FIX_FIELDS_MESSAGE, "danger")

    return render_template("auth/signup_tradesperson.html", trades=TRADES, errors=errors)


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
