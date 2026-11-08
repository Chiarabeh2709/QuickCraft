import pytest
from flask import abort

from app.models import ROLE_ADMIN, ROLE_TRADESPERSON, VERIFICATION_PENDING, VERIFICATION_VERIFIED


def test_not_found_page_has_navigation_and_home_link(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert b"Page not found" in response.data
    assert b'href="/"' in response.data
    assert b"QuickCraft" in response.data


@pytest.mark.parametrize(
    ("role", "status", "email", "expected"),
    [
        (ROLE_ADMIN, None, "error-admin@example.com", b"not available to administrators"),
        (ROLE_TRADESPERSON, VERIFICATION_VERIFIED, "error-worker@example.com", b"relevant tradesperson"),
        ("customer", None, "error-customer@example.com", b"relevant customer"),
        (ROLE_TRADESPERSON, VERIFICATION_PENDING, "error-pending@example.com", b"waiting for approval"),
    ],
)
def test_forbidden_message_depends_on_role(app, client, login, make_user, role, status, email, expected):
    app.add_url_rule("/test-forbidden", "test_forbidden", lambda: abort(403))
    make_user(email=email, role=role, status=status, email_verified=True)
    login(email)
    response = client.get("/test-forbidden")
    assert response.status_code == 403
    assert expected in response.data
    if role != ROLE_TRADESPERSON or status != VERIFICATION_PENDING:
        assert b"waiting for approval" not in response.data


def test_unconfirmed_customer_message_is_generic(client, login, make_user):
    make_user(email="unconfirmed@example.com", email_verified=False)
    login("unconfirmed@example.com")
    response = client.get("/jobs/new", follow_redirects=True)
    assert response.status_code == 200
    assert b"Please confirm your email to continue." in response.data
    assert b"before posting a job" not in response.data


def test_trade_ids_are_valid_slugs(client, login, make_user):
    make_user(email="slug-customer@example.com", email_verified=True)
    login("slug-customer@example.com")
    response = client.get("/jobs/new")
    assert response.status_code == 200
    assert b'id="trade-carpenter"' in response.data
    assert b'for="trade-carpenter"' in response.data
    assert b'id="trade-Carpenter"' not in response.data


def test_slugify_filter_handles_punctuation(app):
    with app.app_context():
        rendered = app.jinja_env.from_string('{{ value|slugify }}').render(value="Heating engineer!")
    assert rendered == "heating-engineer"
