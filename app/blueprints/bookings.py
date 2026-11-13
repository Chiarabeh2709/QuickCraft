# Feature 4: weekly availability and booking a free slot.
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.blueprints.jobs import has_accepted_quote, matching_jobs_query
from app.decorators import confirmed_customer_required, verified_tradesperson_required
from app.extensions import db
from app.models import (
    DEFAULT_DURATION_MINUTES,
    JOB_IN_PROGRESS,
    JOB_OPEN,
    ROLE_CUSTOMER,
    AvailabilitySlot,
    Booking,
    Job,
    TradespersonProfile,
)

bp = Blueprint("bookings", __name__, url_prefix="/bookings")

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKS_AHEAD = 4
# A small job booked straight into a slot has no quote and so no stated duration.
DEFAULT_LENGTH = timedelta(minutes=DEFAULT_DURATION_MINUTES)
# Render runs on UTC, but a slot at 09:00 means 09:00 for people in Germany.
LOCAL_TIME = ZoneInfo("Europe/Berlin")


def now_local():
    # Without timezone info, to match the plain times stored in Booking.starts_at.
    return datetime.now(LOCAL_TIME).replace(tzinfo=None)


def overlaps_booking(booking, starts_at, ends_at):
    # Touching is fine: 09:00-11:00 and 11:00-13:00 do not overlap.
    return booking.starts_at < ends_at and starts_at < booking.ends_at


def has_overlap(tradesperson_id, starts_at, length=DEFAULT_LENGTH):
    """True if a booking of this tradesperson overlaps [starts_at, starts_at + length)."""
    ends_at = starts_at + length
    # Each booking brings its own stated length, so the comparison is done here and not in SQL.
    bookings = db.session.scalars(db.select(Booking).filter_by(tradesperson_id=tradesperson_id))
    return any(overlaps_booking(booking, starts_at, ends_at) for booking in bookings)


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
    # An appointment from a quote can start anywhere in the slot, not only at its start.
    query = db.select(Booking.starts_at).where(
        Booking.tradesperson_id == slot.profile.user_id, Booking.starts_at > now_local()
    )
    return any(
        starts_at.weekday() == slot.weekday and slot.start_time <= starts_at.time() < slot.end_time
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


def can_cancel(booking):
    # A multi-trade job can still be open while one of its appointments stands.
    return booking.starts_at > now_local() and booking.job.status in (JOB_OPEN, JOB_IN_PROGRESS)


def show_appointments(query):
    upcoming = query.where(Booking.starts_at > now_local()).order_by(Booking.starts_at)
    bookings = db.session.scalars(upcoming).all()
    return render_template("bookings/appointments.html", bookings=bookings, can_cancel=can_cancel)


@confirmed_customer_required
def customer_appointments():
    return show_appointments(db.select(Booking).join(Job).where(Job.customer_id == current_user.id))


@verified_tradesperson_required
def tradesperson_appointments():
    return show_appointments(db.select(Booking).where(Booking.tradesperson_id == current_user.id))


@bp.route("/appointments")
@login_required
def appointments():
    # One address for both roles; the decorators above keep each role's own access rule.
    if current_user.role == ROLE_CUSTOMER:
        return customer_appointments()
    return tradesperson_appointments()


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
    booked = list(db.session.scalars(db.select(Booking).filter_by(tradesperson_id=profile.user_id)))
    now = now_local()
    times = [
        (start, slot)
        for slot in profile.availability
        for start in upcoming_starts(slot, now)
        if not any(overlaps_booking(booking, start, start + DEFAULT_LENGTH) for booking in booked)
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
    if has_overlap(slot.profile.user_id, starts_at) or not save_booking(job, slot, starts_at):
        flash("Sorry, somebody else just booked this slot. Please choose another one.", "warning")
        return redirect(url_for("bookings.slots", job_id=job.id))

    flash("Your slot is booked.", "success")
    return redirect(url_for("bookings.appointments"))


@bp.route("/mine")
@confirmed_customer_required
def my_bookings():
    # The old address of the customer's list, which now shares one page with the tradespeople.
    return redirect(url_for("bookings.appointments"))


def remove_booking(booking):
    job = booking.job
    # With an accepted quote the job stays taken and only needs a new date. A booked slot has no quote to
    # keep, so the job reopens. Deleting the row frees the time; without a quote it also hides the contact data.
    keeps_quote = has_accepted_quote(job)
    db.session.delete(booking)
    if not keeps_quote:
        job.status = JOB_OPEN
    db.session.commit()
    return keeps_quote


@bp.route("/<int:booking_id>/cancel", methods=["POST"])
@login_required
def cancel(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    job = booking.job
    # Ownership check: only the two people this appointment belongs to can cancel it.
    if current_user.id not in (job.customer_id, booking.tradesperson_id):
        abort(403)
    if not can_cancel(booking):
        flash("Only upcoming appointments of running jobs can be cancelled.", "warning")
        return redirect(url_for("bookings.appointments"))
    if remove_booking(booking):
        flash("The appointment was cancelled. The customer now needs to choose a new date and time.", "success")
    else:
        flash("The booking was cancelled and the job is open again.", "success")
    return redirect(url_for("bookings.appointments"))
