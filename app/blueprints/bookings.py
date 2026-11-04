# Feature 4: weekly availability and booking a free slot.
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.blueprints.jobs import has_accepted_quote, matching_jobs_query
from app.decorators import confirmed_customer_required, verified_tradesperson_required
from app.extensions import db
from app.models import (
    JOB_IN_PROGRESS,
    JOB_OPEN,
    AvailabilitySlot,
    Booking,
    Job,
    TradespersonProfile,
)

bp = Blueprint("bookings", __name__, url_prefix="/bookings")

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKS_AHEAD = 4
# Render runs on UTC, but a slot at 09:00 means 09:00 for people in Germany.
LOCAL_TIME = ZoneInfo("Europe/Berlin")


def now_local():
    # Without timezone info, to match the plain times stored in Booking.starts_at.
    return datetime.now(LOCAL_TIME).replace(tzinfo=None)


# --- Tradesperson: weekly availability ----------------------------------------------------


def parse_time(name):
    try:
        return datetime.strptime(request.form.get(name, ""), "%H:%M").time()
    except ValueError:
        return None


def overlaps(slot, weekday, start, end):
    # Touching is fine: 09:00-12:00 and 12:00-15:00 do not overlap.
    return slot.weekday == weekday and start < slot.end_time and slot.start_time < end


def slot_errors(profile, weekday, start, end):
    errors = []
    if weekday not in range(len(WEEKDAYS)):
        errors.append("Please choose a weekday.")
    if start is None or end is None:
        errors.append("Please enter a start and an end time.")
    elif end <= start:
        errors.append("The end time must be after the start time.")
    elif any(overlaps(slot, weekday, start, end) for slot in profile.availability):
        errors.append("This overlaps with one of your other slots.")
    return errors


def has_upcoming_booking(slot):
    # A booking stores no end time, so a replacement slot could overlap it without anyone noticing.
    query = db.select(Booking.starts_at).where(
        Booking.tradesperson_id == slot.profile.user_id, Booking.starts_at > now_local()
    )
    return any(
        starts_at.weekday() == slot.weekday and starts_at.time() == slot.start_time
        for starts_at in db.session.scalars(query)
    )


@bp.route("/availability", methods=["GET", "POST"])
@verified_tradesperson_required
def availability():
    profile = current_user.profile
    if request.method == "POST":
        weekday = request.form.get("weekday", type=int)
        start, end = parse_time("start_time"), parse_time("end_time")
        errors = slot_errors(profile, weekday, start, end)
        if not errors:
            db.session.add(AvailabilitySlot(profile=profile, weekday=weekday, start_time=start, end_time=end))
            db.session.commit()
            flash("Your slot was added.", "success")
            return redirect(url_for("bookings.availability"))
        for error in errors:
            flash(error, "danger")

    slots = sorted(profile.availability, key=lambda slot: (slot.weekday, slot.start_time))
    return render_template("bookings/availability.html", slots=slots, weekdays=WEEKDAYS)


@bp.route("/availability/<int:slot_id>/delete", methods=["POST"])
@verified_tradesperson_required
def delete_slot(slot_id):
    slot = db.get_or_404(AvailabilitySlot, slot_id)
    # Ownership check: tradespeople can only change their own slots.
    if slot.profile_id != current_user.profile.id:
        abort(403)
    if has_upcoming_booking(slot):
        flash("This slot has an upcoming booking, so it cannot be deleted.", "warning")
        return redirect(url_for("bookings.availability"))
    db.session.delete(slot)
    db.session.commit()
    flash("Your slot was deleted.", "success")
    return redirect(url_for("bookings.availability"))


@bp.route("/appointments")
@verified_tradesperson_required
def appointments():
    query = (
        db.select(Booking)
        .where(Booking.tradesperson_id == current_user.id, Booking.starts_at > now_local())
        .order_by(Booking.starts_at)
    )
    return render_template("bookings/appointments.html", bookings=db.session.scalars(query).all())


# --- Customer: find a free slot -----------------------------------------------------------


def own_job(job_id):
    job = db.get_or_404(Job, job_id)
    # Ownership check: customers can only book for their own jobs.
    if job.customer_id != current_user.id:
        abort(403)
    return job


def job_is_bookable(job):
    # A multi-trade job stays open after its first accepted quote, but a booking would close it.
    return job.status == JOB_OPEN and job.is_small and not has_accepted_quote(job)


def offers_job(profile, job):
    # Same query as the job list, so nobody gets a booking for a job they could not even see.
    matching_job = db.session.scalar(matching_jobs_query(profile).where(Job.id == job.id))
    return profile.user.is_verified_tradesperson and matching_job is not None


def upcoming_starts(slot, now):
    """The start of this weekly slot in each of the next weeks, without those already past."""
    days_ahead = (slot.weekday - now.weekday()) % 7
    first_day = now.date() + timedelta(days=days_ahead)
    starts = [datetime.combine(first_day + timedelta(weeks=week), slot.start_time) for week in range(WEEKS_AHEAD)]
    return [start for start in starts if start > now]


def free_times(profile):
    """Every free (start, slot) pair of this tradesperson, soonest first."""
    query = db.select(Booking.starts_at).filter_by(tradesperson_id=profile.user_id)
    booked = set(db.session.scalars(query))
    now = now_local()
    times = [
        (start, slot) for slot in profile.availability for start in upcoming_starts(slot, now) if start not in booked
    ]
    return sorted(times, key=lambda entry: entry[0])


def offers_for(job):
    """Matching tradespeople who have at least one free time, as (profile, times) pairs."""
    profiles = db.session.scalars(db.select(TradespersonProfile)).all()
    offers = [(profile, free_times(profile)) for profile in profiles if offers_job(profile, job)]
    return [(profile, times) for profile, times in offers if times]


@bp.route("/job/<int:job_id>")
@confirmed_customer_required
def slots(job_id):
    job = own_job(job_id)
    if not job_is_bookable(job):
        flash("Only open small jobs can be booked.", "warning")
        return redirect(url_for("jobs.my_jobs"))
    return render_template("bookings/slots.html", job=job, offers=offers_for(job))


# --- Customer: book and cancel ------------------------------------------------------------


def requested_start(slot):
    try:
        return datetime.combine(date.fromisoformat(request.form.get("date", "")), slot.start_time)
    except ValueError:
        return None


def slot_is_offered(slot, starts_at, job):
    # The form can be changed by hand, so its slot and date are checked again here.
    return starts_at in upcoming_starts(slot, now_local()) and offers_job(slot.profile, job)


def save_booking(job, slot, starts_at):
    """Returns False if somebody else got this slot first."""
    job.status = JOB_IN_PROGRESS
    db.session.add(Booking(job_id=job.id, tradesperson_id=slot.profile.user_id, starts_at=starts_at))
    try:
        # No "is it free?" check first: two requests could both pass it. Only the unique constraint is safe.
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


@bp.route("/job/<int:job_id>/book", methods=["POST"])
@confirmed_customer_required
def book(job_id):
    job = own_job(job_id)
    if not job_is_bookable(job):
        flash("Only open small jobs can be booked.", "warning")
        return redirect(url_for("jobs.my_jobs"))

    slot = db.session.scalar(db.select(AvailabilitySlot).filter_by(id=request.form.get("slot_id", type=int)))
    starts_at = requested_start(slot) if slot else None
    if slot is None or not slot_is_offered(slot, starts_at, job):
        flash("This slot is not available.", "warning")
        return redirect(url_for("bookings.slots", job_id=job.id))
    if not save_booking(job, slot, starts_at):
        flash("Sorry, somebody else just booked this slot. Please choose another one.", "warning")
        return redirect(url_for("bookings.slots", job_id=job.id))

    flash("Your slot is booked.", "success")
    return redirect(url_for("bookings.my_bookings"))


@bp.route("/mine")
@confirmed_customer_required
def my_bookings():
    query = (
        db.select(Booking)
        .join(Job)
        .where(Job.customer_id == current_user.id)
        .order_by(Booking.starts_at.desc())
    )
    return render_template(
        "bookings/my_bookings.html",
        bookings=db.session.scalars(query).all(),
        now=now_local(),
        job_in_progress=JOB_IN_PROGRESS,
    )


@bp.route("/<int:booking_id>/cancel", methods=["POST"])
@confirmed_customer_required
def cancel(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    job = booking.job
    # Ownership check: customers can only cancel their own bookings.
    if job.customer_id != current_user.id:
        abort(403)
    if booking.starts_at <= now_local() or job.status != JOB_IN_PROGRESS:
        flash("Only upcoming bookings can be cancelled.", "warning")
        return redirect(url_for("bookings.my_bookings"))
    # Deleting the row frees the slot and also hides the customer's contact data again.
    db.session.delete(booking)
    job.status = JOB_OPEN
    db.session.commit()
    flash("Your booking was cancelled.", "success")
    return redirect(url_for("bookings.my_bookings"))
