import pytest

from app import create_app
from config import TestConfig


@pytest.fixture
def app():
    app = create_app(TestConfig)
    # Keep a context open so tests can use db.session directly.
    with app.app_context():
        yield app


@pytest.fixture
def client(app):
    return app.test_client()
