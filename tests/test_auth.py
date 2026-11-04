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

CUSTOMER_FORM = {"name": "Anna", "email": "anna@example.com", "password": "correct-horse"}
TRADESPERSON_FORM = {
    "name": "Ben",
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
