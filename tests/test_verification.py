import pytest

from app.decorators import admin_required, verified_tradesperson_required
from app.extensions import db
from app.models import (
    ROLE_ADMIN,
    ROLE_CUSTOMER,
    ROLE_TRADESPERSON,
    VERIFICATION_PENDING,
    VERIFICATION_REJECTED,
    VERIFICATION_VERIFIED,
    TradespersonProfile,
    User,
)

ADMIN = {"email": "admin@example.com", "role": ROLE_ADMIN}
TRADESPERSON_ROLES = [
    (ROLE_CUSTOMER, None, 403),
    (ROLE_ADMIN, None, 403),
    (ROLE_TRADESPERSON, None, 403),
    (ROLE_TRADESPERSON, VERIFICATION_PENDING, 403),
    (ROLE_TRADESPERSON, VERIFICATION_REJECTED, 403),
    (ROLE_TRADESPERSON, VERIFICATION_VERIFIED, 200),
]


@pytest.fixture
def protected_routes(app):
    # No real route uses these decorators yet, so the test adds its own.
    app.add_url_rule("/only-verified", "only_verified", verified_tradesperson_required(lambda: "ok"))
    app.add_url_rule("/only-admin", "only_admin", admin_required(lambda: "ok"))


@pytest.fixture
def pending_id(app, make_user):
    user_id = make_user(email="ben@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_PENDING)
    with app.app_context():
        db.session.add(
            TradespersonProfile(
                user_id=user_id, company_name="Ben Plumbing", service_area="Berlin", qualification_reference="HWK-1"
            )
        )
        db.session.commit()
    return user_id


def status_of(app, user_id):
    with app.app_context():
        return db.session.get(User, user_id).verification_status


@pytest.mark.parametrize("role, status, expected", TRADESPERSON_ROLES)
def test_verified_tradesperson_required(client, make_user, login, protected_routes, role, status, expected):
    make_user(email="user@example.com", role=role, status=status)
    login("user@example.com")
    assert client.get("/only-verified").status_code == expected


def test_verified_tradesperson_required_sends_anonymous_to_login(client, protected_routes):
    response = client.get("/only-verified")
    assert response.status_code == 302
    assert "/auth/login" in response.location


@pytest.mark.parametrize(
    "role, status, expected",
    [(ROLE_ADMIN, None, 200), (ROLE_CUSTOMER, None, 403), (ROLE_TRADESPERSON, VERIFICATION_VERIFIED, 403)],
)
def test_admin_required(client, make_user, login, protected_routes, role, status, expected):
    make_user(email="user@example.com", role=role, status=status)
    login("user@example.com")
    assert client.get("/only-admin").status_code == expected


def test_admin_page_sends_anonymous_to_login(client):
    response = client.get("/admin/tradespeople")
    assert response.status_code == 302
    assert "/auth/login" in response.location


@pytest.mark.parametrize(
    "role, status", [(ROLE_CUSTOMER, None), (ROLE_TRADESPERSON, VERIFICATION_VERIFIED)]
)
def test_only_admin_can_open_admin_page(client, make_user, login, role, status):
    make_user(email="user@example.com", role=role, status=status)
    login("user@example.com")
    assert client.get("/admin/tradespeople").status_code == 403


def test_admin_page_lists_only_pending(client, make_user, login, pending_id):
    make_user(**ADMIN)
    make_user(email="done@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED)
    login(ADMIN["email"])
    page = client.get("/admin/tradespeople").data
    assert b"Ben Plumbing" in page
    assert b"HWK-1" in page
    assert b"done@example.com" not in page


def test_admin_can_approve(app, client, make_user, login, pending_id):
    make_user(**ADMIN)
    login(ADMIN["email"])
    client.post(f"/admin/tradespeople/{pending_id}/approve")
    assert status_of(app, pending_id) == VERIFICATION_VERIFIED


def test_admin_can_reject(app, client, make_user, login, pending_id):
    make_user(**ADMIN)
    login(ADMIN["email"])
    client.post(f"/admin/tradespeople/{pending_id}/reject")
    assert status_of(app, pending_id) == VERIFICATION_REJECTED


def test_tradesperson_cannot_approve_themselves(app, client, login, pending_id):
    login("ben@example.com")
    assert client.post(f"/admin/tradespeople/{pending_id}/approve").status_code == 403
    assert status_of(app, pending_id) == VERIFICATION_PENDING


def test_customer_cannot_approve(app, client, make_user, login, pending_id):
    make_user(email="anna@example.com")
    login("anna@example.com")
    assert client.post(f"/admin/tradespeople/{pending_id}/approve").status_code == 403
    assert status_of(app, pending_id) == VERIFICATION_PENDING


def test_approve_only_works_by_post(client, make_user, login, pending_id):
    make_user(**ADMIN)
    login(ADMIN["email"])
    assert client.get(f"/admin/tradespeople/{pending_id}/approve").status_code == 405


def test_rejected_tradesperson_cannot_be_approved_later(app, client, make_user, login):
    # A stale admin tab must not undo an earlier decision.
    user_id = make_user(email="ben@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_REJECTED)
    make_user(**ADMIN)
    login(ADMIN["email"])
    client.post(f"/admin/tradespeople/{user_id}/approve")
    assert status_of(app, user_id) == VERIFICATION_REJECTED


def test_approving_a_customer_does_nothing(app, client, make_user, login):
    user_id = make_user(email="anna@example.com")
    make_user(**ADMIN)
    login(ADMIN["email"])
    client.post(f"/admin/tradespeople/{user_id}/approve")
    assert status_of(app, user_id) is None
