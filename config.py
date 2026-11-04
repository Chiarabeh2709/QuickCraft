import os

from dotenv import load_dotenv

# Load .env here so `flask run` and a production server read the same settings.
load_dotenv()


class Config:
    # No default on purpose: a public site with a known key lets anyone forge logins.
    SECRET_KEY = os.environ.get("SECRET_KEY")
    # A relative SQLite path ends up in the instance/ folder, which git ignores.
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///quickcraft.db")


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-only-key"
    # In-memory, so tests start empty and never touch the real database.
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
