# Feature 2: posting, listing and managing jobs.
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from app.blueprints.messages import can_write, find_thread, is_eligible, mark_seen
from app.decorators import confirmed_customer_required, verified_tradesperson_required
from app.extensions import db
from app.models import (
    JOB_CANCELLED,
    JOB_IN_PROGRESS,
    JOB_OPEN,
    QUOTE_ACCEPTED,
    TRADES,
    Booking,
    Job,
    JobTrade,
    Quote,
)

bp = Blueprint("jobs", __name__, url_prefix="/jobs")

URGENCY_NORMAL = "normal"
URGENCY_URGENT = "urgent"

MAX_SHORT_TEXT = 120
MAX_DESCRIPTION = 2000


def form_value(name):
    return request.form.get(name, "").strip()


def selected_trades():
    # Going through TRADES drops invented values and duplicates, and keeps a stable order.
    chosen = request.form.getlist("trades")
    return [trade for trade in TRADES if trade in chosen]


def job_errors(trades):
    errors = []
    if not form_value("title"):
        errors.append("Please give your job a title.")
    if not form_value("description"):
        errors.append("Please describe the job.")
    if not form_value("city"):
        errors.append("Please enter the city.")
    if not trades:
        errors.append("Please choose at least one trade.")
    # The inputs have maxlength, but a hand-made request does not.
    if len(form_value("title")) > MAX_SHORT_TEXT or len(form_value("city")) > MAX_SHORT_TEXT:
        errors.append(f"Title and city can have at most {MAX_SHORT_TEXT} characters.")
    if len(form_value("description")) > MAX_DESCRIPTION:
        errors.append(f"The description can have at most {MAX_DESCRIPTION} characters.")
    return errors


def build_job(trades):
    return Job(
        customer_id=current_user.id,
        title=form_value("title"),
        description=form_value("description"),
        city=form_value("city"),
        urgency=URGENCY_URGENT if request.form.get("urgent") else URGENCY_NORMAL,
        is_small=bool(request.form.get("small")),
        trades=[JobTrade(trade=trade) for trade in trades],
    )


def has_accepted_quote(job):
    query = db.select(Quote.id).join(JobTrade).where(JobTrade.job_id == job.id, Quote.status == QUOTE_ACCEPTED)
    return db.session.scalar(query.limit(1)) is not None


@bp.app_template_global()
def missing_appointments(job):
    """Accepted quotes on a running job whose tradesperson has no appointment, because it was cancelled."""
    if job.status not in (JOB_OPEN, JOB_IN_PROGRESS):
        return []
    # Worked out, not stored: on a multi-trade job one appointment can be missing while another stands.
    booked = {booking.tradesperson_id for booking in job.bookings}
    accepted = [quote for job_trade in job.trades for quote in job_trade.quotes if quote.status == QUOTE_ACCEPTED]
    return [quote for quote in accepted if quote.tradesperson_id not in booked]


# --- Customer: post and manage own jobs ---------------------------------------------------


@bp.route("/new", methods=["GET", "POST"])
@confirmed_customer_required
def new():
    if request.method == "POST":
        trades = selected_trades()
        errors = job_errors(trades)
        if not errors:
            db.session.add(build_job(trades))
            db.session.commit()
            flash("Your job is posted.", "success")
            return redirect(url_for("jobs.my_jobs"))
        for error in errors:
            flash(error, "danger")

    return render_template("jobs/new.html", trades=TRADES)


@bp.route("/mine")
@confirmed_customer_required
def my_jobs():
    query = db.select(Job).filter_by(customer_id=current_user.id).order_by(Job.created_at.desc(), Job.id.desc())
    return render_template("jobs/my_jobs.html", jobs=db.session.scalars(query).all())


@bp.route("/mine/<int:job_id>")
@confirmed_customer_required
def my_job(job_id):
    job = db.get_or_404(Job, job_id)
    # Ownership check: customers can only open their own jobs.
    if job.customer_id != current_user.id:
        abort(403)
    return render_template("jobs/my_job.html", job=job)


@bp.route("/<int:job_id>/cancel", methods=["POST"])
@confirmed_customer_required
def cancel(job_id):
    job = db.get_or_404(Job, job_id)
    # Ownership check: customers can only change their own jobs.
    if job.customer_id != current_user.id:
        abort(403)
    # Once a quote is accepted or a slot booked the job is no longer open, so a stale page cannot cancel it.
    # A multi-trade job stays open after its first acceptance, so that needs its own check.
    if job.status != JOB_OPEN or has_accepted_quote(job):
        flash("Only open jobs without an accepted quote can be cancelled.", "warning")
        return redirect(url_for("jobs.my_jobs"))
    job.status = JOB_CANCELLED
    db.session.commit()
    flash("Your job was cancelled.", "success")
    return redirect(url_for("jobs.my_jobs"))


# --- Tradesperson: find jobs --------------------------------------------------------------


def service_cities(profile):
    # The signup field is free text, so "Berlin, Potsdam" means two cities.
    return [city.strip() for city in (profile.service_area or "").split(",") if city.strip()]


def matching_jobs_query(profile, trade=None, city=None):
    """Open jobs that need one of the tradesperson's trades in one of their cities."""
    trades = profile.approved_trades if profile else []
    cities = service_cities(profile) if profile else []
    # A filter only narrows the list, so choosing a trade or city you do not serve gives no jobs.
    if trade:
        trades = [t for t in trades if t == trade]
    if city:
        cities = [c for c in cities if c.lower() == city.lower()]
    return (
        db.select(Job)
        .where(
            Job.status == JOB_OPEN,
            func.lower(Job.city).in_([c.lower() for c in cities]),
            Job.trades.any(JobTrade.trade.in_(trades)),
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
    )


def has_contact_access(job, user):
    """True once the customer accepted this tradesperson's quote or booked their slot."""
    accepted_quote = (
        db.select(Quote.id)
        .join(JobTrade)
        .where(JobTrade.job_id == job.id, Quote.tradesperson_id == user.id, Quote.status == QUOTE_ACCEPTED)
    )
    booking = db.select(Booking.id).where(Booking.job_id == job.id, Booking.tradesperson_id == user.id)
    return (
        db.session.scalar(accepted_quote.limit(1)) is not None or db.session.scalar(booking.limit(1)) is not None
    )


@bp.route("/")
@verified_tradesperson_required
def job_list():
    profile = current_user.profile
    trade = request.args.get("trade", "").strip()
    city = request.args.get("city", "").strip()
    jobs = db.session.scalars(matching_jobs_query(profile, trade, city)).all()
    return render_template(
        "jobs/list.html",
        jobs=jobs,
        my_trades=profile.approved_trades if profile else [],
        my_cities=service_cities(profile) if profile else [],
        selected_trade=trade,
        selected_city=city,
    )


@bp.route("/<int:job_id>")
@verified_tradesperson_required
def detail(job_id):
    job = db.get_or_404(Job, job_id)
    show_contact = has_contact_access(job, current_user)
    # Same query as the list, so the detail page can never show a job the list would hide.
    in_my_list = db.session.scalar(matching_jobs_query(current_user.profile).where(Job.id == job.id))
    if not show_contact and in_my_list is None:
        abort(404)
    # Only their own: on a multi-trade job the other trades' appointments stay with the customer.
    my_bookings = [booking for booking in job.bookings if booking.tradesperson_id == current_user.id]
    thread = find_thread(job.id, current_user.id)
    mark_seen(thread)
    return render_template(
        "jobs/detail.html",
        job=job,
        show_contact=show_contact,
        my_bookings=my_bookings,
        thread=thread,
        has_thread_access=is_eligible(job, current_user),
        writable=can_write(job, current_user),
        tradesperson_id=current_user.id,
    )
