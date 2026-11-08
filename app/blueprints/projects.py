# Feature 5: multi-trade project page and combined timeline.
from datetime import date

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.decorators import confirmed_customer_required, verified_tradesperson_required
from app.extensions import db
from app.models import (
    Project,
    ProjectComment,
    ProjectSchedule,
    Quote,
    QUOTE_ACCEPTED,
    ROLE_CUSTOMER,
    User,
)

bp = Blueprint("projects", __name__, url_prefix="/projects")


def ensure_project(job):
    accepted = db.session.scalars(
        db.select(Quote).join(Quote.job_trade).where(
            Quote.job_trade.has(job_id=job.id), Quote.status == QUOTE_ACCEPTED
        )
    ).all()
    trade_ids = {quote.job_trade_id for quote in accepted}
    if len(trade_ids) < 2:
        return None
    project = job.project
    if project is None:
        project = Project(job_id=job.id)
        db.session.add(project)
        db.session.flush()
    return project


def project_or_404(project_id):
    project = db.session.get(Project, project_id)
    if project is None:
        abort(404)
    return project


def accepted_quote_for_user(project, user_id):
    return db.session.scalar(
        db.select(Quote)
        .join(Quote.job_trade)
        .where(
            Quote.job_trade.has(job_id=project.job_id),
            Quote.tradesperson_id == user_id,
            Quote.status == QUOTE_ACCEPTED,
        )
    )


def member_required(project):
    if current_user.id == project.job.customer_id:
        return
    if current_user.role == "tradesperson" and accepted_quote_for_user(project, current_user.id):
        return
    abort(403)


@bp.get("/<int:project_id>")
@login_required
def detail(project_id):
    project = project_or_404(project_id)
    member_required(project)
    project.schedules.sort(key=lambda schedule: schedule.start_date)
    project.comments.sort(key=lambda comment: comment.created_at)
    return render_template("projects/detail.html", project=project)


@bp.post("/<int:project_id>/schedule")
@verified_tradesperson_required
def propose_schedule(project_id):
    project = project_or_404(project_id)
    quote = accepted_quote_for_user(project, current_user.id)
    if quote is None:
        abort(403)
    try:
        start_date = date.fromisoformat(request.form["start_date"])
        end_date = date.fromisoformat(request.form["end_date"])
    except (KeyError, ValueError):
        abort(400)
    if end_date < start_date:
        abort(400)
    schedule = db.session.scalar(
        db.select(ProjectSchedule).where(
            ProjectSchedule.project_id == project.id,
            ProjectSchedule.job_trade_id == quote.job_trade_id,
        )
    )
    if schedule is None:
        schedule = ProjectSchedule(
            project_id=project.id,
            job_trade_id=quote.job_trade_id,
            tradesperson_id=current_user.id,
            start_date=start_date,
            end_date=end_date,
        )
        db.session.add(schedule)
    else:
        schedule.start_date = start_date
        schedule.end_date = end_date
    db.session.commit()
    return redirect(url_for("projects.detail", project_id=project.id))


@bp.post("/<int:project_id>/comments")
@login_required
def add_comment(project_id):
    project = project_or_404(project_id)
    member_required(project)
    body = request.form.get("body", "").strip()
    if not body:
        abort(400)
    db.session.add(ProjectComment(project_id=project.id, author_id=current_user.id, body=body))
    db.session.commit()
    return redirect(url_for("projects.detail", project_id=project.id))


@bp.get("/job/<int:job_id>")
@login_required
def for_job(job_id):
    from app.models import Job

    job = db.session.get(Job, job_id)
    if job is None:
        abort(404)
    # Check ownership and eligibility before ensure_project can create anything.
    if job.customer_id != current_user.id:
        abort(403)
    accepted = db.session.scalar(
        db.select(Quote.id)
        .join(Quote.job_trade)
        .where(Quote.job_trade.has(job_id=job.id), Quote.status == QUOTE_ACCEPTED)
    )
    accepted_trade_count = db.session.scalar(
        db.select(func.count(func.distinct(Quote.job_trade_id)))
        .join(Quote.job_trade)
        .where(Quote.job_trade.has(job_id=job.id), Quote.status == QUOTE_ACCEPTED)
    )
    if accepted is None or accepted_trade_count < 2:
        abort(403)
    project = ensure_project(job)
    if project is None:
        abort(404)
    db.session.commit()
    return redirect(url_for("projects.detail", project_id=project.id))
