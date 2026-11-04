# Feature 1: registration, login and the simulated email confirmation.
from flask import Blueprint

bp = Blueprint("auth", __name__, url_prefix="/auth")
