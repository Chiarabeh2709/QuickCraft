# Feature 6: reviews after a job is done.
from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import current_user

from app.decorators import confirmed_customer_required
from app.extensions import db
from app.models import JOB_DONE, Job, Quote, QUOTE_ACCEPTED, Review, ROLE_TRADESPERSON, User

bp = Blueprint("reviews", __name__, url_prefix="/reviews")


def job_or_404(job_id):
    job = db.session.get(Job, job_id)
    if job is None:
        abort(404)
    return job


def did_job(job, tradesperson_id):
    return db.session.scalar(
        db.select(Quote.id)
        .join(Quote.job_trade)
        .where(
            Quote.job_trade.has(job_id=job.id),
            Quote.tradesperson_id == tradesperson_id,
            Quote.status == QUOTE_ACCEPTED,
        )
    ) is not None


@bp.post("/jobs/<int:job_id>/done")
@confirmed_customer_required
def mark_done(job_id):
    job = job_or_404(job_id)
    if job.customer_id != current_user.id:
        abort(403)
    if job.status != JOB_DONE:
        job.status = JOB_DONE
        db.session.commit()
    return redirect(url_for("reviews.job_reviews", job_id=job.id))


@bp.get("/jobs/<int:job_id>")
@confirmed_customer_required
def job_reviews(job_id):
    job = job_or_404(job_id)
    if job.customer_id != current_user.id:
        abort(403)
    return render_template("reviews/job_reviews.html", job=job)


@bp.post("/jobs/<int:job_id>/tradespeople/<int:tradesperson_id>")
@confirmed_customer_required
def add_review(job_id, tradesperson_id):
    job = job_or_404(job_id)
    if job.customer_id != current_user.id or job.status != JOB_DONE:
        abort(403)
    tradesperson = db.session.get(User, tradesperson_id)
    if tradesperson is None or tradesperson.role != ROLE_TRADESPERSON or not did_job(job, tradesperson_id):
        abort(403)
    if db.session.scalar(
        db.select(Review).where(Review.job_id == job.id, Review.tradesperson_id == tradesperson_id)
    ):
        abort(409)
    try:
        stars = int(request.form.get("stars", ""))
    except ValueError:
        abort(400)
    if stars not in range(1, 6):
        abort(400)
    db.session.add(
        Review(
            job_id=job.id,
            tradesperson_id=tradesperson_id,
            stars=stars,
            comment=request.form.get("comment", "").strip() or None,
        )
    )
    db.session.commit()
    return redirect(url_for("reviews.tradesperson_profile", tradesperson_id=tradesperson_id))


def tradespeople_query(search=""):
    query = db.select(User).where(User.role == ROLE_TRADESPERSON)
    if search:
        query = query.where(User.name.ilike(f"%{search}%"))
    return db.session.scalars(query.order_by(User.name)).all()


def rating_for(user):
    reviews = user.reviews_received
    return round(sum(review.stars for review in reviews) / len(reviews), 1) if reviews else None


@bp.get("/tradespeople")
def search_tradespeople():
    search = request.args.get("q", "").strip()
    people = tradespeople_query(search)
    return render_template("reviews/search.html", people=people, ratings={person.id: rating_for(person) for person in people}, search=search)


@bp.get("/tradespeople/<int:tradesperson_id>")
def tradesperson_profile(tradesperson_id):
    person = db.session.get(User, tradesperson_id)
    if person is None or person.role != ROLE_TRADESPERSON:
        abort(404)
    reviews = db.session.scalars(
        db.select(Review).where(Review.tradesperson_id == person.id).order_by(Review.created_at.desc())
    ).all()
    average = round(sum(review.stars for review in reviews) / len(reviews), 1) if reviews else None
    return render_template("reviews/profile.html", person=person, reviews=reviews, average=average)
