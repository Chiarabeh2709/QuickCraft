# Tradesperson search and profiles.
# Phone and address are private: nothing here or in the profile templates may read them.
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from app.blueprints.jobs import form_value, selected_trades, service_cities
from app.decorators import confirmed_customer_required, verified_tradesperson_required
from app.extensions import db
from app.models import (
    ROLE_TRADESPERSON,
    TRADE_APPROVED,
    TRADE_PENDING,
    TRADES,
    VERIFICATION_VERIFIED,
    TradespersonProfile,
    TradespersonTrade,
    User,
)

bp = Blueprint("profiles", __name__, url_prefix="/profiles")

MAX_DESCRIPTION = 1000
MAX_SERVICE_AREA = 120


# --- Customer: search ---------------------------------------------------------------------


def find_profiles(trade, city):
    query = (
        db.select(TradespersonProfile)
        .join(User)
        # In the query itself, so pending and rejected tradespeople can never reach the results.
        .where(User.role == ROLE_TRADESPERSON, User.verification_status == VERIFICATION_VERIFIED)
        # Without an approved trade they offer nothing yet, so they have no place in the results.
        .where(TradespersonProfile.trades.any(status=TRADE_APPROVED))
        .order_by(func.lower(TradespersonProfile.company_name), TradespersonProfile.id)
    )
    if trade:
        query = query.where(TradespersonProfile.trades.any(trade=trade, status=TRADE_APPROVED))
    profiles = db.session.scalars(query).all()
    if city:
        # In Python, because the service area is free text: "Berlin, Potsdam" means two cities.
        profiles = [p for p in profiles if city.lower() in [c.lower() for c in service_cities(p)]]
    return profiles


@bp.route("/")
@confirmed_customer_required
def search():
    trade = request.args.get("trade", "").strip()
    city = request.args.get("city", "").strip()
    return render_template(
        "profiles/search.html",
        profiles=find_profiles(trade, city),
        trades=TRADES,
        selected_trade=trade,
        selected_city=city,
    )


# --- Profile page -------------------------------------------------------------------------


@bp.route("/<int:profile_id>")
def detail(profile_id):
    profile = db.get_or_404(TradespersonProfile, profile_id)
    # 404, not 403: a page for a pending or rejected tradesperson should look like it does not exist.
    if not profile.user.is_verified_tradesperson:
        abort(404)
    is_owner = current_user.is_authenticated and current_user.id == profile.user_id
    from app.blueprints.bookings import free_times
    from app.models import Review

    reviews = db.session.scalars(
        db.select(Review).where(Review.tradesperson_id == profile.user_id)
        .order_by(Review.created_at.desc(), Review.id.desc()).limit(5)
    ).all()
    review_count = db.session.scalar(
        db.select(func.count(Review.id)).where(Review.tradesperson_id == profile.user_id)
    ) or 0
    total_stars = db.session.scalar(
        db.select(func.sum(Review.stars)).where(Review.tradesperson_id == profile.user_id)
    ) or 0
    average_rating = round(total_stars / review_count, 1) if review_count else None
    return render_template(
        "profiles/detail.html", profile=profile, is_owner=is_owner,
        free_slots=free_times(profile), reviews=reviews,
        average_rating=average_rating, review_count=review_count,
        booking_job_id=request.args.get("job_id", type=int),
    )


@bp.route("/me")
@verified_tradesperson_required
def my_profile():
    if current_user.profile is None:
        abort(404)
    return redirect(url_for("profiles.detail", profile_id=current_user.profile.id))


# --- Tradesperson: edit own profile -------------------------------------------------------


def profile_errors(trades):
    errors = {}
    if len(form_value("description")) > MAX_DESCRIPTION:
        errors["description"] = f"The description can have at most {MAX_DESCRIPTION} characters."
    if not trades:
        errors["trades"] = "Please choose at least one trade."
    # A value like "," is not empty, but it names no city, so no job or search could ever match it.
    if not form_value("service_area").strip(" ,"):
        errors["service_area"] = "Please enter your city or service area."
    elif len(form_value("service_area")) > MAX_SERVICE_AREA:
        errors["service_area"] = f"The service area can have at most {MAX_SERVICE_AREA} characters."
    return errors


def update_trades(profile, chosen):
    """Saves the difference to the form and returns the names of the added trades."""
    # Only the differences: replacing every row would insert before deleting and break the unique constraint,
    # and kept rows hold on to their status.
    existing = [entry.trade for entry in profile.trades]
    kept = [entry for entry in profile.trades if entry.trade in chosen]
    added = [trade for trade in chosen if trade not in existing]
    # Always pending, set here and never read from the form, so a crafted POST cannot approve a trade.
    profile.trades = kept + [TradespersonTrade(trade=trade, status=TRADE_PENDING) for trade in added]
    return added


@bp.route("/<int:profile_id>/edit", methods=["GET", "POST"])
@verified_tradesperson_required
def edit(profile_id):
    profile = db.get_or_404(TradespersonProfile, profile_id)
    # Ownership check: tradespeople can only change their own profile.
    if profile.user_id != current_user.id:
        abort(403)

    errors = {}
    if request.method == "POST":
        chosen = selected_trades()
        errors = profile_errors(chosen)
        if not errors:
            # Only these three fields: the company name and qualification reference were checked by the admin.
            profile.description = form_value("description")
            profile.service_area = form_value("service_area")
            added = update_trades(profile, chosen)
            db.session.commit()
            flash("Your profile is saved.", "success")
            if added:
                flash(f"Waiting for admin approval: {', '.join(added)}.", "info")
            return redirect(url_for("profiles.detail", profile_id=profile.id))
        flash("Please correct the fields marked below.", "danger")

    return render_template(
        "profiles/edit.html",
        profile=profile,
        trades=TRADES,
        errors=errors,
        trade_status={entry.trade: entry.status for entry in profile.trades},
        # After a failed submit, show what was typed instead of what is saved.
        description=request.form.get("description", profile.description or ""),
        service_area=request.form.get("service_area", profile.service_area or ""),
        chosen_trades=(
            request.form.getlist("trades") if request.method == "POST" else [entry.trade for entry in profile.trades]
        ),
    )
