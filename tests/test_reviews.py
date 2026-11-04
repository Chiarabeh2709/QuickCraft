from datetime import date

import pytest

from app.extensions import db
from app.models import JOB_OPEN, Job, JobTrade, Quote, QUOTE_ACCEPTED, ROLE_TRADESPERSON, Review, VERIFICATION_VERIFIED


@pytest.fixture
def review_fixture(app, make_user):
    customer = make_user(email="reviewer@example.com", email_verified=True)
    worker = make_user(email="worker@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED)
    other = make_user(email="other@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED)
    with app.app_context():
        job = Job(customer_id=customer, city="Lisbon", description="Fix sink", urgency="soon", status=JOB_OPEN)
        db.session.add(job)
        db.session.flush()
        trade = JobTrade(job_id=job.id, trade="Plumber")
        db.session.add(trade)
        db.session.flush()
        db.session.add(Quote(job_trade_id=trade.id, tradesperson_id=worker, price_cents=100, earliest_start=date.today(), status=QUOTE_ACCEPTED))
        db.session.commit()
        return {"job": job.id, "customer": "reviewer@example.com", "worker": worker, "other": other}


def test_only_customer_can_mark_done(client, login, review_fixture):
    login(review_fixture["worker"] and "worker@example.com")
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/done").status_code == 403
    client.post("/auth/logout")
    login(review_fixture["customer"])
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/done").status_code == 302


def test_review_requires_done_job_and_is_only_once(client, login, review_fixture, app):
    login(review_fixture["customer"])
    job = review_fixture["job"]
    assert client.post(f"/reviews/jobs/{job}/tradespeople/{review_fixture['worker']}", data={"stars": "5"}).status_code == 403
    client.post(f"/reviews/jobs/{job}/done")
    assert client.post(f"/reviews/jobs/{job}/tradespeople/{review_fixture['worker']}", data={"stars": "5", "comment": "Great work"}).status_code == 302
    assert client.post(f"/reviews/jobs/{job}/tradespeople/{review_fixture['worker']}", data={"stars": "4"}).status_code == 409
    with app.app_context():
        assert db.session.scalar(db.select(Review).where(Review.job_id == job)).stars == 5


@pytest.mark.parametrize("stars", ["0", "6", "bad"])
def test_review_stars_must_be_one_to_five(client, login, review_fixture, stars):
    login(review_fixture["customer"])
    client.post(f"/reviews/jobs/{review_fixture['job']}/done")
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/tradespeople/{review_fixture['worker']}", data={"stars": stars}).status_code == (400 if stars != "bad" else 400)


def test_only_the_tradesperson_who_did_job_can_be_reviewed(client, login, review_fixture):
    login(review_fixture["customer"])
    client.post(f"/reviews/jobs/{review_fixture['job']}/done")
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/tradespeople/{review_fixture['other']}", data={"stars": "5"}).status_code == 403


def test_profile_and_search_show_average_and_reviews(client, login, review_fixture):
    login(review_fixture["customer"])
    client.post(f"/reviews/jobs/{review_fixture['job']}/done")
    client.post(f"/reviews/jobs/{review_fixture['job']}/tradespeople/{review_fixture['worker']}", data={"stars": "5", "comment": "Great work"})
    profile = client.get(f"/reviews/tradespeople/{review_fixture['worker']}")
    search = client.get("/reviews/tradespeople?q=Test")
    assert b"5.0" in profile.data and b"Great work" in profile.data
    assert b"5.0" in search.data and b"Test User" in search.data
