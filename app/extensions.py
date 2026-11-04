from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

# Created here, without an app, so models and blueprints can import them
# without importing the app factory (which would be a circular import).
db = SQLAlchemy()
login_manager = LoginManager()


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(connection, _record):
    # SQLite ignores foreign keys unless each connection switches them on.
    connection.execute("PRAGMA foreign_keys=ON")
