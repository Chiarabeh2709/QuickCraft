from flask import Blueprint, abort, render_template, request

from app.blueprints.bookings import free_times
from app.extensions import db
from app.models import ROLE_TRADESPERSON, Review, User

bp = Blueprint("profiles", __name__, url_prefix="/profiles")


@bp.get("/<int:user_id>")
def detail(user_id):
    person = db.session.get(User, user_id)
    if person is None or person.role != ROLE_TRADESPERSON or person.profile is None:
        abort(404)
    reviews = db.session.scalars(
        db.select(Review)
        .where(Review.tradesperson_id == person.id)
        .order_by(Review.created_at.desc())
        .limit(5)
    ).all()
    all_reviews = person.reviews_received
    average = round(sum(review.stars for review in all_reviews) / len(all_reviews), 1) if all_reviews else None
    job_id = request.args.get("job_id", type=int)
    return render_template(
        "profiles/detail.html",
        person=person,
        average=average,
        reviews=reviews,
        free_times=free_times(person.profile),
        job_id=job_id,
    )
