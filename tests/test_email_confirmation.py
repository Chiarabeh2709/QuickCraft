import pytest

from app import create_app
from app.decorators import confirmed_customer_required
from app.extensions import db
from app.models import ROLE_ADMIN, ROLE_CUSTOMER, ROLE_TRADESPERSON, VERIFICATION_PENDING, VERIFICATION_VERIFIED, User
from config import TestConfig

EMAIL = "user@example.com"
CONFIRM_URL = "/auth/confirm-email"


@pytest.fixture
def post_job_route(app):
    # The real job-posting route does not exist yet, so the test adds a stand-in.
    app.add_url_rule("/post-job", "post_job", confirmed_customer_required(lambda: "ok"), methods=["GET", "POST"])


def get_user(app, user_id):
    with app.app_context():
        user = db.session.get(User, user_id)
        return user.email_verified, user.verification_status


def test_unconfirmed_customer_cannot_post_job(client, make_user, login, post_job_route):
    make_user(email=EMAIL)
    login(EMAIL)
    response = client.post("/post-job")
    assert response.status_code == 302
    assert response.location.endswith(CONFIRM_URL)


def test_confirmed_customer_can_post_job(client, make_user, login, post_job_route):
    make_user(email=EMAIL, email_verified=True)
    login(EMAIL)
    assert client.post("/post-job").status_code == 200


def test_customer_can_post_job_after_clicking_confirm(client, make_user, login, post_job_route):
    make_user(email=EMAIL)
    login(EMAIL)
    client.post(CONFIRM_URL)
    assert client.post("/post-job").status_code == 200


def test_anonymous_visitor_cannot_post_job(client, post_job_route):
    response = client.post("/post-job")
    assert response.status_code == 302
    assert "/auth/login" in response.location


@pytest.mark.parametrize(
    "role, status", [(ROLE_TRADESPERSON, VERIFICATION_VERIFIED), (ROLE_ADMIN, None)]
)
def test_only_customers_can_post_job_even_if_confirmed(client, make_user, login, post_job_route, role, status):
    make_user(email=EMAIL, role=role, status=status, email_verified=True)
    login(EMAIL)
    assert client.post("/post-job").status_code == 403


@pytest.mark.parametrize(
    "signup_url, form",
    [
        ("/auth/signup/customer", {}),
        (
            "/auth/signup/tradesperson",
            {
                "company_name": "Ben Plumbing",
                "trade": "Plumber",
                "service_area": "Berlin",
                "qualification_reference": "HWK-1",
            },
        ),
    ],
)
def test_signup_goes_to_confirm_page_unconfirmed(app, client, signup_url, form):
    account = {
        "name": "Test",
        "email": EMAIL,
        "password": "correct-horse",
        "phone": "+49 170 5550123",
        "street": "Hauptstrasse 12",
        "postal_code": "10115",
        "city": "Berlin",
    }
    response = client.post(signup_url, data={**account, **form}, follow_redirects=True)
    assert response.request.path == CONFIRM_URL
    assert b"Confirm my email" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(User)).email_verified is False


@pytest.mark.parametrize("role, status", [(ROLE_CUSTOMER, None), (ROLE_TRADESPERSON, VERIFICATION_PENDING)])
def test_confirm_button_sets_email_verified(app, client, make_user, login, role, status):
    user_id = make_user(email=EMAIL, role=role, status=status)
    login(EMAIL)
    client.post(CONFIRM_URL)
    assert get_user(app, user_id)[0] is True


def test_confirming_email_does_not_verify_a_tradesperson(app, client, make_user, login):
    # Two separate checks: a confirmed email must not skip the admin's approval.
    user_id = make_user(email=EMAIL, role=ROLE_TRADESPERSON, status=VERIFICATION_PENDING)
    login(EMAIL)
    client.post(CONFIRM_URL)
    assert get_user(app, user_id) == (True, VERIFICATION_PENDING)


def test_opening_the_page_does_not_confirm(app, client, make_user, login):
    user_id = make_user(email=EMAIL)
    login(EMAIL)
    assert client.get(CONFIRM_URL).status_code == 200
    assert get_user(app, user_id)[0] is False


def test_confirming_only_affects_the_logged_in_user(app, client, make_user, login):
    make_user(email=EMAIL)
    other_id = make_user(email="other@example.com")
    login(EMAIL)
    client.post(CONFIRM_URL)
    assert get_user(app, other_id)[0] is False


def test_anonymous_visitor_cannot_confirm(client):
    response = client.post(CONFIRM_URL)
    assert response.status_code == 302
    assert "/auth/login" in response.location


def test_login_sends_unconfirmed_user_to_confirm_page(make_user, login):
    make_user(email=EMAIL)
    assert login(EMAIL).location.endswith(CONFIRM_URL)


def test_login_sends_confirmed_user_home(make_user, login):
    make_user(email=EMAIL, email_verified=True)
    assert login(EMAIL).location == "/"


def test_confirmed_user_skips_the_confirm_page(client, make_user, login):
    make_user(email=EMAIL, email_verified=True)
    login(EMAIL)
    assert client.get(CONFIRM_URL).location == "/"


def test_confirm_without_csrf_token_is_rejected():
    class CsrfOn(TestConfig):
        WTF_CSRF_ENABLED = True

    app = create_app(CsrfOn)
    with app.app_context():
        user = User(email=EMAIL, name="Test", role=ROLE_CUSTOMER)
        user.set_password("correct-horse")
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    client = app.test_client()
    # Logging in also needs a token here, so put the login straight into the session.
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True

    assert client.post(CONFIRM_URL).status_code == 400
    assert get_user(app, user_id)[0] is False
