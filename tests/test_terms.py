import pytest

from app.extensions import db
from app.models import (
    ROLE_ADMIN,
    ROLE_CUSTOMER,
    ROLE_TRADESPERSON,
    TERMS_VERSION,
    VERIFICATION_VERIFIED,
    User,
    find_user_by_email,
)

PASSWORD = "correct-horse"
CONTACT = {"phone": "+49 170 5550123", "street": "Hauptstrasse 12", "postal_code": "10115", "city": "Berlin"}
CUSTOMER_FORM = {"name": "Anna", "email": "anna@example.com", "password": PASSWORD, **CONTACT}
TRADESPERSON_FORM = {
    "name": "Ben",
    "email": "ben@example.com",
    "password": PASSWORD,
    **CONTACT,
    "company_name": "Ben Plumbing",
    "trade": "Plumber",
    "service_area": "Berlin",
    "qualification_reference": "HWK-12345",
}
SIGNUPS = [
    ("/auth/signup/customer", CUSTOMER_FORM, "anna@example.com"),
    ("/auth/signup/tradesperson", TRADESPERSON_FORM, "ben@example.com"),
]


def make_unaccepted_user(app, email="new@example.com", role=ROLE_CUSTOMER, version=None, status=None):
    """A user who has not accepted yet, or only an older version of the terms."""
    with app.app_context():
        user = User(email=email, name="Test", role=role, verification_status=status, email_verified=True)
        user.set_password(PASSWORD)
        if version:
            user.terms_version = version
        db.session.add(user)
        db.session.commit()
        return user.id


def terms_state(app, email):
    with app.app_context():
        user = find_user_by_email(email)
        return user.terms_version, user.terms_accepted_at


# --- The text is public -------------------------------------------------------------------


def test_terms_page_is_public_and_shows_its_version(client):
    response = client.get("/terms")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Terms of Use" in page and TERMS_VERSION in page


def test_every_page_links_to_the_terms(client):
    assert 'href="/terms"' in client.get("/").get_data(as_text=True)


# --- Nobody signs up without accepting ----------------------------------------------------


@pytest.mark.parametrize("url, form, email", SIGNUPS)
def test_signup_form_has_an_unticked_required_checkbox_with_a_link(client, url, form, email):
    page = client.get(url).get_data(as_text=True)

    assert 'name="accept_terms"' in page
    box = page[page.index('name="accept_terms"') : page.index("Terms of Use")]
    assert "required" in box and "checked" not in box
    assert 'href="/terms"' in page


@pytest.mark.parametrize("url, form, email", SIGNUPS)
def test_signup_without_accepting_creates_no_account(app, client, url, form, email):
    response = client.post(url, data=form)

    assert response.status_code == 200
    assert "Please accept the Terms of Use to create an account." in response.get_data(as_text=True)
    with app.app_context():
        assert find_user_by_email(email) is None


@pytest.mark.parametrize("url, form, email", SIGNUPS)
def test_signup_with_accepting_stores_when_and_which_version(app, client, url, form, email):
    response = client.post(url, data={**form, "accept_terms": "1"})

    assert response.status_code == 302
    version, accepted_at = terms_state(app, email)
    assert version == TERMS_VERSION
    assert accepted_at is not None


def test_checkbox_stays_ticked_after_another_mistake(client):
    page = client.post("/auth/signup/customer", data={**CUSTOMER_FORM, "password": "short", "accept_terms": "1"})

    html = page.get_data(as_text=True)
    box = html[html.index('name="accept_terms"') : html.index("Terms of Use")]
    assert "checked" in box


# --- Nobody uses the site without accepting the current version ---------------------------


@pytest.mark.parametrize("role, status", [(ROLE_CUSTOMER, None), (ROLE_TRADESPERSON, VERIFICATION_VERIFIED)])
@pytest.mark.parametrize("url", ["/", "/jobs/mine", "/jobs/", "/bookings/appointments", "/profiles/"])
def test_a_user_who_has_not_accepted_is_sent_to_the_acceptance_page(app, client, login, role, status, url):
    make_unaccepted_user(app, role=role, status=status)
    login("new@example.com")

    response = client.get(url)

    assert response.status_code == 302
    assert response.location.endswith("/terms/accept")


def test_an_older_version_does_not_count(app, client, login):
    make_unaccepted_user(app, version="2000-01-01")
    login("new@example.com")

    assert client.get("/").location.endswith("/terms/accept")


def test_text_acceptance_page_and_logout_stay_reachable(app, client, login):
    make_unaccepted_user(app)
    login("new@example.com")

    assert client.get("/terms").status_code == 200
    assert client.get("/terms/accept").status_code == 200
    assert client.post("/auth/logout").status_code == 302
    assert client.get("/jobs/mine").status_code == 302  # logged out: sent to the login, not to the terms


def test_accepting_lets_the_user_in_and_is_stored(app, client, login):
    make_unaccepted_user(app)
    login("new@example.com")

    response = client.post("/terms/accept", data={"accept_terms": "1"})

    assert response.status_code == 302 and not response.location.endswith("/terms/accept")
    assert terms_state(app, "new@example.com")[0] == TERMS_VERSION
    assert client.get("/").status_code == 200


@pytest.mark.parametrize("data", [{}, {"accept_terms": ""}])
def test_accepting_needs_the_box(app, client, login, data):
    make_unaccepted_user(app)
    login("new@example.com")

    response = client.post("/terms/accept", data=data)

    assert response.status_code == 200
    assert "Please accept the Terms of Use to continue." in response.get_data(as_text=True)
    assert terms_state(app, "new@example.com") == (None, None)
    assert client.get("/").location.endswith("/terms/accept")


def test_acceptance_page_needs_a_login(client):
    response = client.get("/terms/accept")

    assert response.status_code == 302 and "/auth/login" in response.location


def test_user_who_accepted_is_not_asked_again(client, make_user, login):
    make_user(email="done@example.com", email_verified=True)  # the shared helper counts as accepted
    login("done@example.com")

    assert client.get("/terms/accept").status_code == 302
    assert client.get("/").status_code == 200


def test_the_admin_is_not_asked(app, client, login):
    make_unaccepted_user(app, email="admin@example.com", role=ROLE_ADMIN)
    login("admin@example.com")

    assert client.get("/admin/tradespeople").status_code == 200


def test_visitors_are_not_affected(client):
    assert client.get("/").status_code == 200
    assert client.get("/auth/login").status_code == 200
