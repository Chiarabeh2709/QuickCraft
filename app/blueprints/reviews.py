# Feature 6: reviews after a job is done.
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from app.decorators import confirmed_customer_required
from app.blueprints.jobs import missing_appointments
from app.extensions import db
from app.models import (
    JOB_DONE,
    Booking,
    QUOTE_ACCEPTED,
    ROLE_TRADESPERSON,
    TRADE_APPROVED,
    VERIFICATION_VERIFIED,
    Job,
    Quote,
    Review,
    TradespersonProfile,
    User,
)

bp = Blueprint("reviews", __name__, url_prefix="/reviews")


def job_or_404(job_id):
    job = db.session.get(Job, job_id)
    if job is None:
        abort(404)
    return job


def did_job(job, tradesperson_id):
    accepted = db.session.scalar(
        db.select(Quote.id)
        .join(Quote.job_trade)
        .where(
            Quote.job_trade.has(job_id=job.id),
            Quote.tradesperson_id == tradesperson_id,
            Quote.status == QUOTE_ACCEPTED,
        )
    ) is not None
    booked = db.session.scalar(
        db.select(Booking.id).where(
            Booking.job_id == job.id,
            Booking.tradesperson_id == tradesperson_id,
        )
    ) is not None
    return accepted or booked


@bp.post("/jobs/<int:job_id>/done")
@confirmed_customer_required
def mark_done(job_id):
    job = job_or_404(job_id)
    if job.customer_id != current_user.id:
        abort(403)
    if job.status != "in_progress":
        abort(403)
    if not job.bookings or missing_appointments(job):
        abort(403)
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
    if not request.form.get("stars"):
        # The form requires a rating, so this only happens without the browser check: no review is saved.
        flash("Please choose a star rating before you submit your review.", "warning")
        return redirect(url_for("reviews.job_reviews", job_id=job.id))
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
    flash("Your review was submitted.", "success")
    # Back to the job, because the tradesperson's own page is a 404 once they have no approved trade.
    return redirect(url_for("reviews.job_reviews", job_id=job.id))


def listed_profiles(search=""):
    query = (
        db.select(TradespersonProfile)
        .join(User)
        # In the query itself, so pending and rejected tradespeople can never reach this list.
        .where(User.role == ROLE_TRADESPERSON, User.verification_status == VERIFICATION_VERIFIED)
        .where(TradespersonProfile.trades.any(status=TRADE_APPROVED))
        .order_by(func.lower(TradespersonProfile.company_name), TradespersonProfile.id)
    )
    if search:
        # By company only: searching personal names would let people test who works where.
        query = query.where(TradespersonProfile.company_name.icontains(search, autoescape=True))
    return db.session.scalars(query).all()


def rating_for(user):
    reviews = user.reviews_received
    return round(sum(review.stars for review in reviews) / len(reviews), 1) if reviews else None


@bp.get("/tradespeople")
@confirmed_customer_required
def search_tradespeople():
    search = request.args.get("q", "").strip()
    profiles = listed_profiles(search)
    ratings = {profile.user_id: rating_for(profile.user) for profile in profiles}
    return render_template("reviews/search.html", profiles=profiles, ratings=ratings, search=search)


def verified_profile_or_404(user_id):
    profile = db.session.scalar(db.select(TradespersonProfile).filter_by(user_id=user_id))
    # 404, not 403: someone who is not listed should look like they do not exist.
    if profile is None or not profile.user.is_verified_tradesperson or not profile.approved_trades:
        abort(404)
    return profile


@bp.get("/tradespeople/<int:tradesperson_id>")
@confirmed_customer_required
def tradesperson_profile(tradesperson_id):
    profile = verified_profile_or_404(tradesperson_id)
    reviews = db.session.scalars(
        db.select(Review).where(Review.tradesperson_id == profile.user_id).order_by(Review.created_at.desc())
    ).all()
    average = round(sum(review.stars for review in reviews) / len(reviews), 1) if reviews else None
    return render_template("reviews/profile.html", profile=profile, reviews=reviews, average=average)
