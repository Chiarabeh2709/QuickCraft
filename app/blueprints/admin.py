# Admin: approving or rejecting tradespeople.
from flask import Blueprint, flash, redirect, render_template, url_for

from app.decorators import admin_required
from app.extensions import db
from app.models import (
    ROLE_TRADESPERSON,
    VERIFICATION_PENDING,
    VERIFICATION_REJECTED,
    VERIFICATION_VERIFIED,
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
    return render_template("admin/pending_tradespeople.html", tradespeople=db.session.scalars(query))


def decide(user_id, new_status, message):
    user = db.session.get(User, user_id)
    # Only a pending tradesperson can change, so a stale page cannot undo an earlier decision.
    if user and user.role == ROLE_TRADESPERSON and user.verification_status == VERIFICATION_PENDING:
        user.verification_status = new_status
        db.session.commit()
        flash(f"{user.name} {message}.", "success")
    else:
        flash("This tradesperson is no longer pending.", "warning")
    return redirect(url_for("admin.pending_tradespeople"))


@bp.route("/tradespeople/<int:user_id>/approve", methods=["POST"])
@admin_required
def approve(user_id):
    return decide(user_id, VERIFICATION_VERIFIED, "was approved")


@bp.route("/tradespeople/<int:user_id>/reject", methods=["POST"])
@admin_required
def reject(user_id):
    return decide(user_id, VERIFICATION_REJECTED, "was rejected")
