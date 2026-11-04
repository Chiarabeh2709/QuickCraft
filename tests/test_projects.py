from datetime import date

import pytest

from app.extensions import db
from app.models import (
    JOB_OPEN,
    Project,
    Job,
    JobTrade,
    Quote,
    QUOTE_ACCEPTED,
    ROLE_CUSTOMER,
    ROLE_TRADESPERSON,
    VERIFICATION_VERIFIED,
    User,
)


@pytest.fixture
def project_fixture(app, make_user):
    customer_id = make_user(email="customer@example.com", email_verified=True)
    first_id = make_user(
        email="first@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED
    )
    second_id = make_user(
        email="second@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED
    )
    outsider_id = make_user(
        email="outsider@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED
    )
    with app.app_context():
        job = Job(customer_id=customer_id, city="Lisbon", description="Renovate kitchen", urgency="soon", status=JOB_OPEN)
        db.session.add(job)
        db.session.flush()
        plumbing = JobTrade(job_id=job.id, trade="Plumber")
        electrical = JobTrade(job_id=job.id, trade="Electrician")
        db.session.add_all([plumbing, electrical])
        db.session.flush()
        db.session.add_all(
            [
                Quote(job_trade_id=plumbing.id, tradesperson_id=first_id, price_cents=100, earliest_start=date.today(), status=QUOTE_ACCEPTED),
                Quote(job_trade_id=electrical.id, tradesperson_id=second_id, price_cents=100, earliest_start=date.today(), status=QUOTE_ACCEPTED),
            ]
        )
        project = Project(job_id=job.id)
        db.session.add(project)
        db.session.commit()
        return {
            "job_id": job.id,
            "project_id": project.id,
            "customer": "customer@example.com",
            "first": "first@example.com",
            "second": "second@example.com",
            "outsider": "outsider@example.com",
        }


def test_project_is_created_for_two_accepted_trades(client, login, project_fixture, app):
    with app.app_context():
        project = db.session.get(Project, project_fixture["project_id"])
        db.session.delete(project)
        db.session.commit()
    login(project_fixture["customer"])
    response = client.get(f"/projects/job/{project_fixture['job_id']}")
    assert response.status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(Project).where(Project.job_id == project_fixture["job_id"])) is not None


def test_project_is_visible_to_customer(client, login, project_fixture):
    login(project_fixture["customer"])
    assert client.get(f"/projects/{project_fixture['project_id']}").status_code == 200


def test_project_is_visible_to_each_accepted_tradesperson(client, login, project_fixture):
    for email in (project_fixture["first"], project_fixture["second"]):
        login(email)
        assert client.get(f"/projects/{project_fixture['project_id']}").status_code == 200
        client.post("/auth/logout")


@pytest.mark.parametrize("email", ["outsider@example.com"])
def test_project_rejects_non_member_tradesperson(client, login, project_fixture, email):
    login(email)
    assert client.get(f"/projects/{project_fixture['project_id']}").status_code == 403


def test_project_rejects_anonymous(client, project_fixture):
    response = client.get(f"/projects/{project_fixture['project_id']}")
    assert response.status_code == 302
    assert "/auth/login" in response.location


def test_accepted_tradesperson_can_propose_dates_and_comment(client, login, project_fixture):
    login(project_fixture["first"])
    project_id = project_fixture["project_id"]
    assert client.post(
        f"/projects/{project_id}/schedule", data={"start_date": "2026-12-01", "end_date": "2026-12-03"}
    ).status_code == 302
    assert client.post(f"/projects/{project_id}/comments", data={"body": "I will bring the materials."}).status_code == 302
    page = client.get(f"/projects/{project_id}")
    assert b"2026-12-01" in page.data
    assert b"I will bring the materials." in page.data


def test_customer_can_comment_but_cannot_propose_dates(client, login, project_fixture):
    login(project_fixture["customer"])
    project_id = project_fixture["project_id"]
    assert client.post(f"/projects/{project_id}/comments", data={"body": "Please coordinate access."}).status_code == 302
    assert client.post(
        f"/projects/{project_id}/schedule", data={"start_date": "2026-12-01", "end_date": "2026-12-03"}
    ).status_code == 403


def test_non_member_cannot_comment_or_propose_dates(client, login, project_fixture):
    login(project_fixture["outsider"])
    project_id = project_fixture["project_id"]
    assert client.post(f"/projects/{project_id}/comments", data={"body": "No access."}).status_code == 403
    assert client.post(
        f"/projects/{project_id}/schedule", data={"start_date": "2026-12-01", "end_date": "2026-12-03"}
    ).status_code == 403
