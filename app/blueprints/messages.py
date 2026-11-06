# Message threads between a customer and a tradesperson on a job. Not real-time: a reload shows new messages.
from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.decorators import confirmed_customer_required, verified_tradesperson_required
from app.extensions import db
from app.models import (
    JOB_IN_PROGRESS,
    JOB_OPEN,
    ROLE_CUSTOMER,
    ROLE_TRADESPERSON,
    Booking,
    Job,
    JobTrade,
    Message,
    MessageThread,
    Quote,
    User,
    utcnow,
)

bp = Blueprint("messages", __name__, url_prefix="/messages")

MAX_MESSAGE = 1000
RATE_LIMIT = 10
RATE_WINDOW = timedelta(minutes=1)


@bp.app_template_filter("display_name")
def display_name(user):
    # Tradespeople by company, customers by first name: the thread needs no more than that.
    if user.role == ROLE_TRADESPERSON:
        return user.profile.company_name
    return user.name.split()[0]


# --- Who may take part --------------------------------------------------------------------


def has_quote_or_booking(job, tradesperson):
    quote = db.select(Quote.id).join(JobTrade).where(JobTrade.job_id == job.id, Quote.tradesperson_id == tradesperson.id)
    booking = db.select(Booking.id).where(Booking.job_id == job.id, Booking.tradesperson_id == tradesperson.id)
    return db.session.scalar(quote.limit(1)) is not None or db.session.scalar(booking.limit(1)) is not None


def is_eligible(job, tradesperson):
    return tradesperson.is_verified_tradesperson and has_quote_or_booking(job, tradesperson)


def can_write(job, tradesperson):
    return job.status in (JOB_OPEN, JOB_IN_PROGRESS) and is_eligible(job, tradesperson)


def find_thread(job_id, tradesperson_id):
    return db.session.scalar(db.select(MessageThread).filter_by(job_id=job_id, tradesperson_id=tradesperson_id))


def check_access(job, tradesperson, thread):
    # Ownership check. The customer keeps an existing thread even if the quote is gone, so consent can be revoked.
    if current_user.id == job.customer_id:
        allowed = thread is not None or is_eligible(job, tradesperson)
    else:
        allowed = current_user.id == tradesperson.id and is_eligible(job, tradesperson)
    if not allowed:
        abort(403)


def job_and_tradesperson(job_id, tradesperson_id):
    job = db.get_or_404(Job, job_id)
    tradesperson = db.get_or_404(User, tradesperson_id)
    if tradesperson.role != ROLE_TRADESPERSON:
        abort(404)
    return job, tradesperson


# --- Unread messages ----------------------------------------------------------------------


def mark_seen(thread):
    if thread is None:
        return
    if current_user.id == thread.tradesperson_id:
        thread.tradesperson_last_seen_at = utcnow()
    else:
        thread.customer_last_seen_at = utcnow()
    db.session.commit()


@bp.app_template_global()
def unread_count(thread):
    is_tradesperson = current_user.id == thread.tradesperson_id
    last_seen = thread.tradesperson_last_seen_at if is_tradesperson else thread.customer_last_seen_at
    theirs = [message for message in thread.messages if message.sender_id != current_user.id]
    return sum(1 for message in theirs if last_seen is None or message.created_at > last_seen)


@bp.app_template_global()
def conversations(job):
    """The tradespeople the customer can talk to on this job, each with their thread if it exists."""
    threads = {thread.tradesperson_id: thread for thread in job.threads}
    people = {quote.tradesperson for job_trade in job.trades for quote in job_trade.quotes}
    people |= {booking.tradesperson for booking in job.bookings}
    people = {person for person in people if person.is_verified_tradesperson}
    people |= {thread.tradesperson for thread in job.threads}
    return [(person, threads.get(person.id)) for person in sorted(people, key=display_name)]


# --- Lists --------------------------------------------------------------------------------


def latest_first(threads):
    with_messages = [thread for thread in threads if thread.messages]
    return sorted(with_messages, key=lambda thread: thread.messages[-1].id, reverse=True)


def show_inbox(threads):
    return render_template("messages/inbox.html", threads=latest_first(threads))


@confirmed_customer_required
def customer_inbox():
    query = db.select(MessageThread).join(Job).where(Job.customer_id == current_user.id)
    return show_inbox(db.session.scalars(query).all())


@verified_tradesperson_required
def tradesperson_inbox():
    query = db.select(MessageThread).filter_by(tradesperson_id=current_user.id)
    # A withdrawn quote ends the access, so its thread must not be listed either.
    return show_inbox([t for t in db.session.scalars(query) if is_eligible(t.job, current_user)])


@bp.route("/")
@login_required
def inbox():
    # One address for both roles; the decorators above keep each role's own access rule.
    if current_user.role == ROLE_CUSTOMER:
        return customer_inbox()
    return tradesperson_inbox()


# --- Reading and writing ------------------------------------------------------------------


def over_rate_limit():
    since = utcnow() - RATE_WINDOW
    query = db.select(db.func.count(Message.id)).where(Message.sender_id == current_user.id, Message.created_at > since)
    return db.session.scalar(query) >= RATE_LIMIT


def message_error(body):
    if not body:
        return "Please write a message."
    # The textarea has maxlength, but a hand-made request does not.
    if len(body) > MAX_MESSAGE:
        return f"A message can have at most {MAX_MESSAGE} characters."
    if over_rate_limit():
        return f"You can send at most {RATE_LIMIT} messages per minute. Please wait a moment."
    return None


def save_message(job, tradesperson, thread, body):
    """Returns False if a second request created the thread at the same moment."""
    if thread is None:
        thread = MessageThread(job_id=job.id, tradesperson_id=tradesperson.id)
        db.session.add(thread)
    thread.messages.append(Message(sender_id=current_user.id, body=body))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def show_conversation(job, tradesperson, thread):
    writable = can_write(job, tradesperson)
    shared = thread is not None and thread.phone_shared_at is not None
    return render_template(
        "messages/thread.html",
        job=job,
        tradesperson=tradesperson,
        tradesperson_id=tradesperson.id,
        thread=thread,
        writable=writable,
        shared=shared,
        # Only here and only for this tradesperson, which is what the customer agreed to.
        show_phone=shared and writable and current_user.id == tradesperson.id,
    )


@bp.route("/job/<int:job_id>/tradesperson/<int:tradesperson_id>", methods=["GET", "POST"])
@login_required
def conversation(job_id, tradesperson_id):
    job, tradesperson = job_and_tradesperson(job_id, tradesperson_id)
    thread = find_thread(job.id, tradesperson.id)
    check_access(job, tradesperson, thread)

    if request.method == "GET":
        mark_seen(thread)
        return show_conversation(job, tradesperson, thread)

    if not can_write(job, tradesperson):
        flash("This conversation is read-only.", "warning")
        return redirect(url_for("messages.conversation", job_id=job.id, tradesperson_id=tradesperson.id))
    body = request.form.get("body", "").strip()
    error = message_error(body)
    if error is None:
        if save_message(job, tradesperson, thread, body):
            return redirect(url_for("messages.conversation", job_id=job.id, tradesperson_id=tradesperson.id))
        error = "Your message could not be sent. Please try again."
    flash(error, "danger")
    return show_conversation(job, tradesperson, find_thread(job.id, tradesperson.id))


# --- Sharing the phone number -------------------------------------------------------------


def customers_thread_or_404(job_id, tradesperson_id):
    job, tradesperson = job_and_tradesperson(job_id, tradesperson_id)
    # Ownership check: consent is the customer's own decision, so nobody else can give or take it back.
    if job.customer_id != current_user.id:
        abort(403)
    thread = find_thread(job.id, tradesperson.id)
    if thread is None:
        abort(404)
    return job, tradesperson, thread


@bp.route("/job/<int:job_id>/tradesperson/<int:tradesperson_id>/share-phone", methods=["POST"])
@confirmed_customer_required
def share_phone(job_id, tradesperson_id):
    job, tradesperson, thread = customers_thread_or_404(job_id, tradesperson_id)
    if can_write(job, tradesperson):
        thread.phone_shared_at = utcnow()
        db.session.commit()
        flash(f"Your phone number is now shared with {display_name(tradesperson)}.", "success")
    else:
        flash("This conversation is read-only.", "warning")
    return redirect(url_for("messages.conversation", job_id=job.id, tradesperson_id=tradesperson.id))


@bp.route("/job/<int:job_id>/tradesperson/<int:tradesperson_id>/revoke-phone", methods=["POST"])
@confirmed_customer_required
def revoke_phone(job_id, tradesperson_id):
    # No check of the job's state: taking consent back must always work.
    job, tradesperson, thread = customers_thread_or_404(job_id, tradesperson_id)
    thread.phone_shared_at = None
    db.session.commit()
    flash("Your phone number is no longer shared through this conversation.", "success")
    return redirect(url_for("messages.conversation", job_id=job.id, tradesperson_id=tradesperson.id))
