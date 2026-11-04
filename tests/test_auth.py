import re

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    ROLE_ADMIN,
    ROLE_CUSTOMER,
    ROLE_TRADESPERSON,
    VERIFICATION_PENDING,
    User,
    ensure_admin,
    find_user_by_email,
)
from config import TestConfig

CONTACT_FIELDS = {"phone": "+49 170 5550123", "street": "Hauptstrasse 12", "postal_code": "10115", "city": "Berlin"}
CUSTOMER_FORM = {"name": "Anna", "email": "anna@example.com", "password": "correct-horse", **CONTACT_FIELDS}
TRADESPERSON_FORM = {
    "name": "Ben",
    **CONTACT_FIELDS,
    "company_name": "Ben Plumbing",
    "trade": "Plumber",
    "service_area": "Berlin",
    "qualification_reference": "HWK-12345",
    "email": "ben@example.com",
    "password": "correct-horse",
}


def user_count(app):
    with app.app_context():
        return db.session.query(User).count()


def test_customer_signup_creates_customer(app, client):
    client.post("/auth/signup/customer", data=CUSTOMER_FORM)
    with app.app_context():
        user = find_user_by_email("anna@example.com")
        assert user.role == ROLE_CUSTOMER
        assert user.verification_status is None
        assert user.password_hash != CUSTOMER_FORM["password"]


def test_customer_cannot_sign_up_as_admin(app, client):
    client.post("/auth/signup/customer", data={**CUSTOMER_FORM, "role": ROLE_ADMIN})
    with app.app_context():
        assert find_user_by_email("anna@example.com").role == ROLE_CUSTOMER


def test_tradesperson_signup_starts_pending_with_profile(app, client):
    client.post("/auth/signup/tradesperson", data=TRADESPERSON_FORM)
    with app.app_context():
        user = find_user_by_email("ben@example.com")
        assert user.role == ROLE_TRADESPERSON
        assert user.verification_status == VERIFICATION_PENDING
        assert user.profile.company_name == "Ben Plumbing"
        assert user.profile.service_area == "Berlin"
        assert user.profile.qualification_reference == "HWK-12345"
        assert [t.trade for t in user.profile.trades] == ["Plumber"]


def test_tradesperson_cannot_verify_themselves_at_signup(app, client):
    client.post("/auth/signup/tradesperson", data={**TRADESPERSON_FORM, "verification_status": "verified"})
    with app.app_context():
        assert find_user_by_email("ben@example.com").verification_status == VERIFICATION_PENDING


def test_tradesperson_signup_rejects_unknown_trade(app, client):
    client.post("/auth/signup/tradesperson", data={**TRADESPERSON_FORM, "trade": "Wizard"})
    assert user_count(app) == 0


def test_tradesperson_signup_requires_qualification_reference(app, client):
    client.post("/auth/signup/tradesperson", data={**TRADESPERSON_FORM, "qualification_reference": " "})
    assert user_count(app) == 0


def test_signup_rejects_short_password(app, client):
    client.post("/auth/signup/customer", data={**CUSTOMER_FORM, "password": "short"})
    assert user_count(app) == 0


def test_signup_rejects_duplicate_email_in_any_case(app, client, make_user):
    make_user(email="anna@example.com")
    client.post("/auth/signup/customer", data={**CUSTOMER_FORM, "email": "ANNA@example.com"})
    assert user_count(app) == 1


def test_login_with_correct_password(client, make_user, login):
    make_user(email="anna@example.com")
    assert login("anna@example.com").status_code == 302
    assert b"Log out" in client.get("/").data


def test_login_with_wrong_password_fails(client, make_user):
    make_user(email="anna@example.com")
    response = client.post("/auth/login", data={"email": "anna@example.com", "password": "wrong-password"})
    assert b"Wrong email or password" in response.data
    assert b"Log out" not in client.get("/").data


def test_logout(client, make_user, login):
    make_user(email="anna@example.com")
    login("anna@example.com")
    client.post("/auth/logout")
    assert b"Log out" not in client.get("/").data


def test_pending_tradesperson_sees_waiting_message(client, make_user, login):
    make_user(email="ben@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_PENDING)
    login("ben@example.com")
    assert b"waiting for approval" in client.get("/").data


def test_post_without_csrf_token_is_rejected():
    class CsrfOn(TestConfig):
        WTF_CSRF_ENABLED = True

    app = create_app(CsrfOn)
    response = app.test_client().post("/auth/signup/customer", data=CUSTOMER_FORM)
    assert response.status_code == 400
    assert user_count(app) == 0


def test_admin_is_created_from_config_only_once():
    class WithAdmin(TestConfig):
        ADMIN_EMAIL = "Boss@example.com"
        ADMIN_PASSWORD = "correct-horse"

    app = create_app(WithAdmin)
    with app.app_context():
        assert find_user_by_email("boss@example.com").role == ROLE_ADMIN
        # A second startup against the same database must not fail or add a user.
        ensure_admin("boss@example.com", "correct-horse")
        assert db.session.query(User).count() == 1


# --- Contact details at sign-up ----------------------------------------------------------

SIGNUP_FORMS = [
    pytest.param("/auth/signup/customer", CUSTOMER_FORM, id="customer"),
    pytest.param("/auth/signup/tradesperson", TRADESPERSON_FORM, id="tradesperson"),
]


def field_is_marked_invalid(page, field):
    return f'id="{field}"' in page and re.search(rf'is-invalid"[^>]*id="{field}"', page) is not None


@pytest.mark.parametrize("url, form", SIGNUP_FORMS)
def test_signup_stores_phone_and_combined_address(app, client, url, form):
    client.post(url, data=form)
    with app.app_context():
        user = find_user_by_email(form["email"])
        assert user.name == form["name"]
        assert user.phone == "+49 170 5550123"
        assert user.address == "Hauptstrasse 12, 10115 Berlin"


@pytest.mark.parametrize("url, form", SIGNUP_FORMS)
@pytest.mark.parametrize(
    "field, message",
    [
        ("name", "enter your full name"),
        ("email", "valid email address"),
        ("phone", "valid phone number"),
        ("street", "street and house number"),
        ("postal_code", "enter your postal code"),
        ("city", "enter your city"),
    ],
)
@pytest.mark.parametrize("blank", ["", "   "])
def test_every_contact_field_is_required(app, client, url, form, field, message, blank):
    response = client.post(url, data={**form, field: blank})
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert message in page
    assert field_is_marked_invalid(page, field)
    assert user_count(app) == 0


@pytest.mark.parametrize("url, form", SIGNUP_FORMS)
@pytest.mark.parametrize("field", ["name", "email", "phone", "street", "postal_code", "city"])
def test_every_contact_field_is_required_when_left_out(app, client, url, form, field):
    data = {key: value for key, value in form.items() if key != field}
    client.post(url, data=data)
    assert user_count(app) == 0


@pytest.mark.parametrize("url, form", SIGNUP_FORMS)
@pytest.mark.parametrize(
    "email",
    ["plain", "no-domain@", "@no-name.de", "no-dot@domain", "two@@example.com", "with space@example.com", "a@b.de\nc"],
)
def test_signup_rejects_invalid_email(app, client, url, form, email):
    response = client.post(url, data={**form, "email": email})
    assert "valid email address" in response.get_data(as_text=True)
    assert user_count(app) == 0


@pytest.mark.parametrize("url, form", SIGNUP_FORMS)
@pytest.mark.parametrize(
    "phone",
    [
        "abc",
        "0170-5550123",
        "(030) 5550123",
        "030/5550123",
        "++49 170 5550123",
        "49+170 5550123",
        "+",
        "12345",
        "1234567890123456",
        "1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6",
        "٠١٧٠٥٥٥٠١٢٣",
    ],
)
def test_signup_rejects_invalid_phone(app, client, url, form, phone):
    response = client.post(url, data={**form, "phone": phone})
    assert "valid phone number" in response.get_data(as_text=True)
    assert user_count(app) == 0


@pytest.mark.parametrize("phone", ["01705550123", "+491705550123", "+49 170 5550123", "0170 555 0123", "123456"])
def test_signup_accepts_valid_phone(app, client, phone):
    client.post("/auth/signup/customer", data={**CUSTOMER_FORM, "phone": phone})
    with app.app_context():
        assert find_user_by_email("anna@example.com").phone == phone


@pytest.mark.parametrize(
    "field, length",
    [("name", 121), ("street", 121), ("postal_code", 11), ("city", 121), ("email", 256), ("phone", 31)],
)
def test_signup_rejects_values_that_are_too_long(app, client, field, length):
    value = {"email": "a" * (length - 12) + "@example.com", "phone": "1 " * 15 + "1"}.get(field, "x" * length)
    assert len(value) == length
    client.post("/auth/signup/customer", data={**CUSTOMER_FORM, field: value})
    assert user_count(app) == 0


def test_longest_allowed_address_fits_its_column(app, client):
    form = {**CUSTOMER_FORM, "street": "s" * 120, "postal_code": "p" * 10, "city": "c" * 120}
    client.post("/auth/signup/customer", data=form)
    with app.app_context():
        assert len(find_user_by_email("anna@example.com").address) <= User.address.type.length


@pytest.mark.parametrize(
    "field, message",
    [
        ("company_name", "enter your company name"),
        ("service_area", "city or service area"),
        ("qualification_reference", "qualification reference number"),
    ],
)
def test_tradesperson_errors_are_shown_next_to_their_field(app, client, field, message):
    page = client.post("/auth/signup/tradesperson", data={**TRADESPERSON_FORM, field: ""}).get_data(as_text=True)
    assert message in page
    assert field_is_marked_invalid(page, field)
    assert user_count(app) == 0


def test_all_errors_are_shown_at_once(client):
    data = {**CUSTOMER_FORM, "name": "", "phone": "abc", "street": ""}
    page = client.post("/auth/signup/customer", data=data).get_data(as_text=True)
    assert "enter your full name" in page
    assert "valid phone number" in page
    assert "street and house number" in page
    assert "Please correct the fields marked below" in page


def test_failed_signup_keeps_entries_but_never_the_password(client):
    page = client.post("/auth/signup/customer", data={**CUSTOMER_FORM, "phone": "abc"}).get_data(as_text=True)
    assert 'value="Hauptstrasse 12"' in page
    assert 'value="abc"' in page
    assert CUSTOMER_FORM["password"] not in page


def test_signup_values_are_escaped_when_refilled(client):
    data = {**CUSTOMER_FORM, "phone": "abc", "street": '"><script>alert(1)</script>'}
    page = client.post("/auth/signup/customer", data=data).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page


@pytest.mark.parametrize("url, form", SIGNUP_FORMS)
def test_signup_form_explains_why_we_ask(client, url, form):
    page = client.get(url).get_data(as_text=True)
    assert "Why we ask" in page
    assert "private" in page


def test_admin_from_environment_needs_no_contact_details():
    class WithAdmin(TestConfig):
        ADMIN_EMAIL = "boss@example.com"
        ADMIN_PASSWORD = "correct-horse"

    app = create_app(WithAdmin)
    with app.app_context():
        assert find_user_by_email("boss@example.com").phone is None
