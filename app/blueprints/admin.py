# Admin: approving or rejecting tradespeople and their added trades.
from flask import Blueprint, flash, redirect, render_template, url_for

from app.decorators import admin_required
from app.extensions import db
from app.models import (
    ROLE_TRADESPERSON,
    TRADE_APPROVED,
    TRADE_PENDING,
    TRADE_REJECTED,
    VERIFICATION_PENDING,
    VERIFICATION_REJECTED,
    VERIFICATION_VERIFIED,
    TradespersonProfile,
    TradespersonTrade,
    User,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/tradespeople")
@admin_required
def pending_tradespeople():
    query = (
        db.select(User)
        .filter_by(role=ROLE_TRADESPERSON, verification_status=VERIFICATION_PENDING)
        .order_by(User.created_at)
    )
    return render_template(
        "admin/pending_tradespeople.html",
        tradespeople=db.session.scalars(query),
        pending_trades=pending_trades(),
    )


def pending_trades():
    # Verified accounts only: the trades of a pending account are decided together with that account.
    query = (
        db.select(TradespersonTrade)
        .join(TradespersonProfile)
        .join(User)
        .where(TradespersonTrade.status == TRADE_PENDING, User.verification_status == VERIFICATION_VERIFIED)
        .order_by(TradespersonTrade.id)
    )
    return db.session.scalars(query).all()


def decide_signup_trades(user, new_status):
    for entry in user.profile.trades if user.profile else []:
        if entry.status == TRADE_PENDING:
            entry.status = new_status


def decide(user_id, new_status, trade_status, message):
    user = db.session.get(User, user_id)
    # Only a pending tradesperson can change, so a stale page cannot undo an earlier decision.
    if user and user.role == ROLE_TRADESPERSON and user.verification_status == VERIFICATION_PENDING:
        user.verification_status = new_status
        decide_signup_trades(user, trade_status)
        db.session.commit()
        flash(f"{user.name} {message}.", "success")
    else:
        flash("This tradesperson is no longer pending.", "warning")
    return redirect(url_for("admin.pending_tradespeople"))


@bp.route("/tradespeople/<int:user_id>/approve", methods=["POST"])
@admin_required
def approve(user_id):
    return decide(user_id, VERIFICATION_VERIFIED, TRADE_APPROVED, "was approved")


@bp.route("/tradespeople/<int:user_id>/reject", methods=["POST"])
@admin_required
def reject(user_id):
    return decide(user_id, VERIFICATION_REJECTED, TRADE_REJECTED, "was rejected")


def decide_trade(trade_id, new_status, message):
    entry = db.session.get(TradespersonTrade, trade_id)
    # Only a pending trade can change, so a stale page cannot undo an earlier decision.
    if entry and entry.status == TRADE_PENDING and entry.profile.user.is_verified_tradesperson:
        entry.status = new_status
        db.session.commit()
        flash(f"{entry.trade} for {entry.profile.company_name} {message}.", "success")
    else:
        flash("This trade is no longer pending.", "warning")
    return redirect(url_for("admin.pending_tradespeople"))


@bp.route("/trades/<int:trade_id>/approve", methods=["POST"])
@admin_required
def approve_trade(trade_id):
    return decide_trade(trade_id, TRADE_APPROVED, "was approved")


@bp.route("/trades/<int:trade_id>/reject", methods=["POST"])
@admin_required
def reject_trade(trade_id):
    return decide_trade(trade_id, TRADE_REJECTED, "was rejected")
