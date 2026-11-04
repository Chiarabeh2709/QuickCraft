# Feature 2: posting and listing jobs.
from flask import Blueprint

bp = Blueprint("jobs", __name__, url_prefix="/jobs")
