import pytest

from app.decorators import admin_required, confirmed_customer_required, verified_tradesperson_required
from app.extensions import db
from app.models import (
    ROLE_ADMIN,
    ROLE_CUSTOMER,
    ROLE_TRADESPERSON,
    TRADE_APPROVED,
    TradespersonProfile,
    TradespersonTrade,
    VERIFICATION_PENDING,
    VERIFICATION_VERIFIED,
)


@pytest.fixture
def protected_pages(app):
    app.add_url_rule("/errors/admin", "errors_admin", admin_required(lambda: "ok"))
    app.add_url_rule("/errors/tradesperson", "errors_tradesperson", verified_tradesperson_required(lambda: "ok"))
    app.add_url_rule("/errors/customer", "errors_customer", confirmed_customer_required(lambda: "ok"))


@pytest.mark.parametrize(
    "role,status,message",
    [
        (ROLE_ADMIN, None, "Administrators cannot access this page."),
        (ROLE_CUSTOMER, None, "This page is not available to customers."),
        (ROLE_TRADESPERSON, VERIFICATION_VERIFIED, "This page is not available to verified tradespeople."),
        (ROLE_TRADESPERSON, VERIFICATION_PENDING, "Your account is waiting for approval."),
    ],
)
def test_forbidden_page_has_role_specific_message(client, make_user, login, protected_pages, role, status, message):
    make_user(email="role@example.com", role=role, status=status, email_verified=True)
    login("role@example.com")
    route = "/errors/tradesperson" if role == ROLE_ADMIN else "/errors/admin"
    response = client.get(route)
    assert response.status_code == 403
    assert message in response.get_data(as_text=True)
    assert "Back to home" in response.get_data(as_text=True)


def test_not_found_page_is_styled_and_links_home(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    page = response.get_data(as_text=True)
    assert "Page not found" in page
    assert "Back to home" in page
    assert "navbar" in page


def test_unconfirmed_customer_gets_generic_confirmation_message(client, make_user, login, protected_pages):
    make_user(email="unconfirmed@example.com")
    login("unconfirmed@example.com")
    response = client.get("/errors/customer", follow_redirects=True)
    assert "Please confirm your email to continue." in response.get_data(as_text=True)
    assert "before posting a job" not in response.get_data(as_text=True)


def test_trade_ids_are_slugged_in_job_and_profile_forms(client, make_user, login, app):
    customer = make_user(email="customer@example.com", email_verified=True)
    login("customer@example.com")
    jobs_page = client.get("/jobs/new").get_data(as_text=True)
    assert 'id="trade-heating-engineer"' in jobs_page
    assert 'for="trade-heating-engineer"' in jobs_page

    client.post("/auth/logout")
    tradesperson = make_user(email="tradesperson@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED, email_verified=True)
    with app.app_context():
        profile = TradespersonProfile(user_id=tradesperson, company_name="Heating Co", service_area="Munich", trades=[TradespersonTrade(trade="Heating engineer", status=TRADE_APPROVED)])
        db.session.add(profile)
        db.session.commit()
        profile_id = profile.id
    login("tradesperson@example.com")
    edit_page = client.get(f"/profiles/{profile_id}/edit").get_data(as_text=True)
    assert 'id="trade-heating-engineer"' in edit_page
    assert 'for="trade-heating-engineer"' in edit_page
