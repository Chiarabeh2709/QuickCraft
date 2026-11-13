import pytest

from app import create_app
from app.extensions import db
from app.models import ROLE_CUSTOMER, User
from config import TestConfig

PASSWORD = "correct-horse"


@pytest.fixture
def app():
    # No app context stays open here: requests would share it, and Flask-Login
    # would then remember the logged-in user from one request to the next.
    return create_app(TestConfig)


@pytest.fixture
def app_ctx(app):
    # For tests that use db.session directly, without sending requests.
    with app.app_context():
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_user(app):
    """Creates a user and returns its id."""

    def _make_user(email="user@example.com", role=ROLE_CUSTOMER, status=None, email_verified=False):
        with app.app_context():
            user = User(
                email=email, name="Test User", role=role, verification_status=status, email_verified=email_verified
            )
            user.set_password(PASSWORD)
            user.accept_terms()  # Otherwise every login would be sent to the acceptance page first.
            db.session.add(user)
            db.session.commit()
            return user.id

    return _make_user


@pytest.fixture
def login(client):
    def _login(email):
        return client.post("/auth/login", data={"email": email, "password": PASSWORD})

    return _login
