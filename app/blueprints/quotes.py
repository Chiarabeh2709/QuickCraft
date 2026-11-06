# Feature 3: sending and comparing quotes.
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.blueprints.bookings import now_local
from app.blueprints.jobs import matching_jobs_query
from app.decorators import confirmed_customer_required, verified_tradesperson_required
from app.extensions import db
from app.models import (
    JOB_IN_PROGRESS,
    JOB_OPEN,
    QUOTE_ACCEPTED,
    QUOTE_PENDING,
    QUOTE_REJECTED,
    Booking,
    Job,
    JobTrade,
    Quote,
)

bp = Blueprint("quotes", __name__, url_prefix="/quotes")

MAX_MESSAGE = 2000
MAX_PRICE_EUROS = 1_000_000


@bp.app_template_filter("euros")
def euros(cents):
    return f"{cents // 100}.{cents % 100:02d}"


# --- Reading the form ---------------------------------------------------------------------


def parse_price_cents():
    # Decimal, not float, because floats cannot store money exactly. Germans type a comma.
    text = request.form.get("price", "").strip().replace(",", ".")
    try:
        price = Decimal(text)
    except InvalidOperation:
        return None
    if not price.is_finite() or not 0 < price <= MAX_PRICE_EUROS:
        return None
    cents = price * 100
    return int(cents) if cents == int(cents) else None


def parse_earliest_start():
    try:
        return date.fromisoformat(request.form.get("earliest_start", ""))
    except ValueError:
        return None


def quote_fields():
    return {
        "price_cents": parse_price_cents(),
        "earliest_start": parse_earliest_start(),
        "message": request.form.get("message", "").strip(),
    }


def field_errors(fields):
    errors = []
    if fields["price_cents"] is None:
        errors.append("Please enter a price above 0, with at most two decimal places.")
    if fields["earliest_start"] is None:
        errors.append("Please enter your earliest start date.")
    elif fields["earliest_start"] < now_local().date():
        errors.append("The earliest start date cannot be in the past.")
    # The textarea has maxlength, but a hand-made request does not.
    if len(fields["message"]) > MAX_MESSAGE:
        errors.append(f"The message can have at most {MAX_MESSAGE} characters.")
    return errors


def flash_errors(errors):
    for error in errors:
        flash(error, "danger")


# --- Tradesperson: send a quote -----------------------------------------------------------


def trade_filled(job_trade):
    return any(quote.status == QUOTE_ACCEPTED for quote in job_trade.quotes)


def quotable_trades(job):
    """The trades on this job that the tradesperson offers and nobody has won yet."""
    my_trades = current_user.profile.approved_trades
    return [job_trade for job_trade in job.trades if job_trade.trade in my_trades and not trade_filled(job_trade)]


def has_quote_on(job):
    # The database only blocks a second quote per trade, but the rule is one quote per job.
    query = (
        db.select(Quote.id)
        .join(JobTrade)
        .where(JobTrade.job_id == job.id, Quote.tradesperson_id == current_user.id)
    )
    return db.session.scalar(query.limit(1)) is not None


def visible_job_or_404(job_id):
    job = db.get_or_404(Job, job_id)
    # Same query as the job list, so a hand-typed URL cannot reach a job the list would hide.
    if db.session.scalar(matching_jobs_query(current_user.profile).where(Job.id == job.id)) is None:
        abort(404)
    return job


def chosen_trade(trades):
    # Looked up in the allowed list, so a changed form cannot quote for another trade.
    job_trade_id = request.form.get("job_trade_id", type=int)
    return next((job_trade for job_trade in trades if job_trade.id == job_trade_id), None)


def save_quote(job_trade, fields):
    """Returns False if the database already holds this quote, for example after a double click."""
    db.session.add(Quote(job_trade_id=job_trade.id, tradesperson_id=current_user.id, **fields))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


@bp.route("/job/<int:job_id>/new", methods=["GET", "POST"])
@verified_tradesperson_required
def new(job_id):
    job = visible_job_or_404(job_id)
    if has_quote_on(job):
        flash("You have already sent a quote for this job.", "warning")
        return redirect(url_for("quotes.my_quotes"))
    trades = quotable_trades(job)
    if not trades:
        flash("Your trade on this job has already been taken.", "warning")
        return redirect(url_for("jobs.job_list"))

    if request.method == "POST":
        fields = quote_fields()
        job_trade = chosen_trade(trades)
        errors = field_errors(fields) + ([] if job_trade else ["Please choose a trade."])
        if not errors:
            if save_quote(job_trade, fields):
                flash("Your quote was sent.", "success")
            else:
                flash("You have already sent a quote for this job.", "warning")
            return redirect(url_for("quotes.my_quotes"))
        flash_errors(errors)

    return render_template("quotes/form.html", job=job, trades=trades, quote=None)


# --- Tradesperson: manage own quotes ------------------------------------------------------


def own_quote(quote_id):
    quote = db.get_or_404(Quote, quote_id)
    # Ownership check: tradespeople can only change their own quotes.
    if quote.tradesperson_id != current_user.id:
        abort(403)
    return quote


@bp.route("/mine")
@verified_tradesperson_required
def my_quotes():
    query = (
        db.select(Quote)
        .filter_by(tradesperson_id=current_user.id)
        .order_by(Quote.created_at.desc(), Quote.id.desc())
    )
    return render_template("quotes/my_quotes.html", quotes=db.session.scalars(query).all())


@bp.route("/<int:quote_id>/edit", methods=["GET", "POST"])
@verified_tradesperson_required
def edit(quote_id):
    quote = own_quote(quote_id)
    if quote.status != QUOTE_PENDING:
        flash("Only pending quotes can be changed.", "warning")
        return redirect(url_for("quotes.my_quotes"))

    if request.method == "POST":
        fields = quote_fields()
        errors = field_errors(fields)
        if not errors:
            quote.price_cents = fields["price_cents"]
            quote.earliest_start = fields["earliest_start"]
            quote.message = fields["message"]
            db.session.commit()
            flash("Your quote was updated.", "success")
            return redirect(url_for("quotes.my_quotes"))
        flash_errors(errors)

    return render_template("quotes/form.html", job=quote.job_trade.job, trades=[], quote=quote)


@bp.route("/<int:quote_id>/withdraw", methods=["POST"])
@verified_tradesperson_required
def withdraw(quote_id):
    quote = own_quote(quote_id)
    if quote.status != QUOTE_PENDING:
        flash("Only pending quotes can be withdrawn.", "warning")
        return redirect(url_for("quotes.my_quotes"))
    # There is no "withdrawn" status: without the row, the tradesperson may quote again.
    db.session.delete(quote)
    db.session.commit()
    flash("Your quote was withdrawn.", "success")
    return redirect(url_for("quotes.my_quotes"))


# --- Customer: compare and accept ---------------------------------------------------------


def can_accept(quote):
    job_trade = quote.job_trade
    return (
        job_trade.job.status == JOB_OPEN
        and quote.status == QUOTE_PENDING
        and quote.tradesperson.is_verified_tradesperson
        and not trade_filled(job_trade)
    )


def sort_key(sort):
    if sort == "price":
        return lambda quote: (quote.price_cents, quote.earliest_start, quote.id)
    return lambda quote: (quote.earliest_start, quote.price_cents, quote.id)


@bp.route("/job/<int:job_id>")
@confirmed_customer_required
def compare(job_id):
    job = db.get_or_404(Job, job_id)
    # Ownership check: only the customer of a job may see its quotes.
    if job.customer_id != current_user.id:
        abort(403)
    sort = request.args.get("sort")
    groups = [(job_trade, sorted(job_trade.quotes, key=sort_key(sort))) for job_trade in job.trades]
    acceptable = {quote.id for _, quotes in groups for quote in quotes if can_accept(quote)}
    return render_template("quotes/compare.html", job=job, groups=groups, acceptable=acceptable, sort=sort)


def accept_quote(quote, starts_at):
    """Returns False if the quote was no longer pending or somebody else just took this time."""
    # The condition sits in the UPDATE itself, so of two simultaneous requests only one can win.
    accepted = db.update(Quote).where(Quote.id == quote.id, Quote.status == QUOTE_PENDING)
    if db.session.execute(accepted.values(status=QUOTE_ACCEPTED)).rowcount == 0:
        db.session.rollback()
        return False

    # Only this trade: on a multi-trade job the quotes for other trades stay in the running.
    others = db.update(Quote).where(
        Quote.job_trade_id == quote.job_trade_id, Quote.id != quote.id, Quote.status == QUOTE_PENDING
    )
    db.session.execute(others.values(status=QUOTE_REJECTED))

    job = quote.job_trade.job
    if all(trade_filled(job_trade) for job_trade in job.trades):
        job.status = JOB_IN_PROGRESS
    # One commit with the quote, so an accepted quote can never end up without its appointment.
    db.session.add(Booking(job_id=job.id, tradesperson_id=quote.tradesperson_id, starts_at=starts_at))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True
