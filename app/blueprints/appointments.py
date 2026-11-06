# Feature 4: confirming an appointment while accepting a quote, and choosing a new one after a cancellation.
from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.bookings import APPOINTMENT_LENGTH, WEEKDAYS, has_overlap, now_local
from app.blueprints.jobs import missing_appointments
from app.blueprints.quotes import accept_quote, can_accept, save_appointment
from app.decorators import confirmed_customer_required
from app.extensions import db
from app.models import Quote

bp = Blueprint("appointments", __name__, url_prefix="/appointments")

NEEDS_CONFIRMATION = "The time still has to be confirmed by the tradesperson."


def requested_start():
    try:
        day = date.fromisoformat(request.form.get("date", ""))
        return datetime.combine(day, datetime.strptime(request.form.get("time", ""), "%H:%M").time())
    except ValueError:
        return None


def fits_slot(slot, starts_at):
    ends_at = starts_at + APPOINTMENT_LENGTH
    return (
        slot.weekday == starts_at.weekday()
        and ends_at.date() == starts_at.date()
        and slot.start_time <= starts_at.time()
        and ends_at.time() <= slot.end_time
    )


def time_error(quote, starts_at):
    """The reason this time cannot be used, or None if it can."""
    if starts_at is None:
        return "Please choose a date and a time."
    if starts_at <= now_local():
        return "The appointment cannot be in the past."
    if starts_at.date() < quote.earliest_start:
        return f"The appointment cannot be before the earliest start date, {quote.earliest_start:%d %b %Y}."
    slots = quote.tradesperson.profile.availability
    # A tradesperson without slots has told us nothing, so any time is allowed for now.
    if slots and not any(fits_slot(slot, starts_at) for slot in slots):
        return "This time is outside the tradesperson's availability."
    if has_overlap(quote.tradesperson_id, starts_at):
        return "The tradesperson already has an appointment at this time. Please choose another one."
    return None


def success_message(quote, done):
    if quote.tradesperson.profile.availability:
        return f"{done} and confirmed the appointment."
    return f"{done}. {NEEDS_CONFIRMATION}"


def own_quote(quote_id):
    quote = db.get_or_404(Quote, quote_id)
    # Ownership check: only the customer of the job can set an appointment for it.
    if quote.job_trade.job.customer_id != current_user.id:
        abort(403)
    return quote


def show_form(quote, rebooking=False):
    slots = sorted(quote.tradesperson.profile.availability, key=lambda slot: (slot.weekday, slot.start_time))
    return render_template(
        "appointments/confirm.html",
        quote=quote,
        job=quote.job_trade.job,
        slots=slots,
        weekdays=WEEKDAYS,
        default_date=max(quote.earliest_start, now_local().date()),
        note=NEEDS_CONFIRMATION,
        rebooking=rebooking,
    )


@bp.route("/quote/<int:quote_id>", methods=["GET", "POST"])
@confirmed_customer_required
def confirm(quote_id):
    quote = own_quote(quote_id)
    job = quote.job_trade.job
    if not can_accept(quote):
        flash("This quote can no longer be accepted.", "warning")
        return redirect(url_for("quotes.compare", job_id=job.id))

    if request.method == "POST":
        starts_at = requested_start()
        error = time_error(quote, starts_at)
        if error is None:
            if accept_quote(quote, starts_at):
                flash(success_message(quote, "You accepted this quote"), "success")
                return redirect(url_for("jobs.my_job", job_id=job.id))
            error = "This quote or time was just taken. Please check and try again."
        flash(error, "danger")

    return show_form(quote)


@bp.route("/quote/<int:quote_id>/rebook", methods=["GET", "POST"])
@confirmed_customer_required
def rebook(quote_id):
    quote = own_quote(quote_id)
    job = quote.job_trade.job
    if quote not in missing_appointments(job):
        flash("This job does not need a new appointment.", "warning")
        return redirect(url_for("jobs.my_job", job_id=job.id))

    if request.method == "POST":
        starts_at = requested_start()
        error = time_error(quote, starts_at)
        if error is None:
            if save_appointment(quote, starts_at):
                flash(success_message(quote, "You chose a new date"), "success")
                return redirect(url_for("jobs.my_job", job_id=job.id))
            error = "This time was just taken. Please choose another one."
        flash(error, "danger")

    return show_form(quote, rebooking=True)
