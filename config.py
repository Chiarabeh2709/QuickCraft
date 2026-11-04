import os

from dotenv import load_dotenv

# Load .env here so `flask run` and a production server read the same settings.
load_dotenv()


class Config:
    # No default on purpose: a public site with a known key lets anyone forge logins.
    SECRET_KEY = os.environ.get("SECRET_KEY")
    # A relative SQLite path ends up in the instance/ folder, which git ignores.
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///quickcraft.db")
    # Signup cannot create admins, so the first admin comes from the environment.
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
    # Render always sets RENDER=true. Local dev runs on plain http, where a secure cookie would break login.
    SESSION_COOKIE_SECURE = os.environ.get("RENDER") == "true"
    SESSION_COOKIE_SAMESITE = "Lax"


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-only-key"
    # In-memory, so tests start empty and never touch the real database.
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    ADMIN_EMAIL = None
    ADMIN_PASSWORD = None
    # Tests post forms directly; one test switches this back on to prove CSRF works.
    WTF_CSRF_ENABLED = False
