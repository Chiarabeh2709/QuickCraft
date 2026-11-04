import sqlite3

import pytest

from app import create_app
from app.extensions import db
from app.models import ROLE_ADMIN, User, find_user_by_email
from config import TestConfig

ADMIN_EMAIL = "admin@example.com"


class WithAdmin(TestConfig):
    ADMIN_EMAIL = ADMIN_EMAIL
    ADMIN_PASSWORD = "correct-horse"


@pytest.fixture
def not_on_render(monkeypatch):
    # So these tests behave the same wherever they run.
    monkeypatch.delenv("RENDER", raising=False)


def emails(app):
    with app.app_context():
        return sorted(db.session.scalars(db.select(User.email)))


# "false" and an empty value are refused too: the rule is "set", so a typo cannot switch the guard off.
@pytest.mark.parametrize("value", ["true", "false", ""])
def test_refuses_to_run_when_render_is_set(app, make_user, monkeypatch, value):
    make_user(email="anna@example.com")
    monkeypatch.setenv("RENDER", value)

    result = app.test_cli_runner().invoke(args=["reset-db"])

    assert result.exit_code != 0
    assert "Refusing to run" in result.output
    assert emails(app) == ["anna@example.com"]


def test_deletes_all_data(app, make_user, not_on_render):
    make_user(email="anna@example.com")
    result = app.test_cli_runner().invoke(args=["reset-db"])
    assert result.exit_code == 0
    assert emails(app) == []


def test_recreates_the_admin(not_on_render):
    app = create_app(WithAdmin)
    with app.app_context():
        db.session.add(User(email="anna@example.com", name="Anna", role="customer", password_hash="x"))
        db.session.commit()

    result = app.test_cli_runner().invoke(args=["reset-db"])

    assert result.exit_code == 0
    assert emails(app) == [ADMIN_EMAIL]
    with app.app_context():
        admin = find_user_by_email(ADMIN_EMAIL)
        assert admin.role == ROLE_ADMIN
        assert admin.check_password("correct-horse")


def test_says_so_when_no_admin_can_be_created(app, not_on_render):
    result = app.test_cli_runner().invoke(args=["reset-db"])
    assert "No admin created" in result.output


def test_works_on_an_outdated_database(tmp_path, not_on_render):
    # The main use: a model changed, so the existing user table no longer matches it.
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE user (id INTEGER PRIMARY KEY, email TEXT)")
    connection.close()

    class OldDatabase(WithAdmin):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{path}"

    app = create_app(OldDatabase)
    result = app.test_cli_runner().invoke(args=["reset-db"])

    assert result.exit_code == 0
    assert emails(app) == [ADMIN_EMAIL]
