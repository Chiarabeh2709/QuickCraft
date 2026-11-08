import pytest

from app.models import ROLE_ADMIN, ROLE_CUSTOMER, ROLE_TRADESPERSON, VERIFICATION_PENDING, VERIFICATION_VERIFIED


@pytest.mark.parametrize(
    ("email", "role", "status", "endpoint", "text"),
    [
        ("admin@example.com", ROLE_ADMIN, None, "/profiles/", "not available to administrators"),
        ("trade@example.com", ROLE_TRADESPERSON, VERIFICATION_VERIFIED, "/profiles/", "not available to verified tradespeople"),
        ("customer@example.com", ROLE_CUSTOMER, None, "/profiles/me", "not available to customers"),
        ("pending@example.com", ROLE_TRADESPERSON, VERIFICATION_PENDING, "/profiles/", "waiting for approval"),
    ],
)
def test_forbidden_page_has_role_specific_message(client, make_user, login, email, role, status, endpoint, text):
    make_user(email=email, role=role, status=status, email_verified=True)
    login(email)
    response = client.get(endpoint)
    assert response.status_code == 403
    assert text in response.get_data(as_text=True)
    assert "Back to home" in response.get_data(as_text=True)


def test_not_found_page_is_styled_and_links_home(client):
    response = client.get("/does-not-exist")
    page = response.get_data(as_text=True)
    assert response.status_code == 404
    assert "Page not found" in page
    assert "Back to home" in page
    assert "navbar" in page


def test_unconfirmed_customer_gets_generic_confirmation_message(client, make_user, login):
    make_user(email="unconfirmed@example.com", role=ROLE_CUSTOMER, email_verified=False)
    login("unconfirmed@example.com")
    response = client.get("/profiles/")
    page = client.get(response.location).get_data(as_text=True)
    assert "Please confirm your email to continue." in page
    assert "before posting a job" not in page


def test_trade_ids_are_valid_slugs(client, make_user, login):
    make_user(email="customer@example.com", role=ROLE_CUSTOMER, email_verified=True)
    login("customer@example.com")
    page = client.get("/jobs/new").get_data(as_text=True)
    assert 'id="trade-heating-engineer"' in page
    assert 'for="trade-heating-engineer"' in page
    assert 'id="trade-Heating engineer"' not in page
