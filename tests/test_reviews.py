from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import Booking, JOB_DONE, JOB_IN_PROGRESS, JOB_OPEN, Job, JobTrade, Quote, QUOTE_ACCEPTED, ROLE_TRADESPERSON, Review, User, VERIFICATION_VERIFIED, TradespersonProfile


@pytest.fixture
def review_fixture(app, make_user):
    customer = make_user(email="reviewer@example.com", email_verified=True)
    worker = make_user(email="worker@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED)
    other = make_user(email="other@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED)
    other_customer = make_user(email="other-customer@example.com", email_verified=True)
    with app.app_context():
        job = Job(customer_id=customer, city="Lisbon", description="Fix sink", urgency="soon", status=JOB_OPEN)
        db.session.add(job)
        db.session.flush()
        trade = JobTrade(job_id=job.id, trade="Plumber")
        db.session.add(trade)
        db.session.flush()
        db.session.add(TradespersonProfile(user_id=worker, company_name="Worker Company"))
        db.session.add(Quote(job_trade_id=trade.id, tradesperson_id=worker, price_cents=100, earliest_start=date.today(), status=QUOTE_ACCEPTED))
        db.session.add(Booking(job_id=job.id, tradesperson_id=worker, starts_at=datetime.combine(date.today(), datetime.min.time())))
        job.status = JOB_IN_PROGRESS
        db.session.commit()
        return {"job": job.id, "customer": "reviewer@example.com", "other_customer": "other-customer@example.com", "worker": worker, "other": other}


def test_only_customer_can_mark_done(client, login, review_fixture):
    login(review_fixture["other_customer"])
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/done").status_code == 403
    client.post("/auth/logout")
    login(review_fixture["customer"])
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/done").status_code == 302


@pytest.mark.parametrize("status", [JOB_OPEN, "cancelled", "done"])
def test_mark_done_requires_in_progress_status(client, login, review_fixture, app, status):
    with app.app_context():
        job = db.session.get(Job, review_fixture["job"])
        job.status = status
        db.session.commit()
    login(review_fixture["customer"])
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/done").status_code == 403


def test_mark_done_requires_a_booking(client, login, review_fixture, app):
    with app.app_context():
        job = db.session.get(Job, review_fixture["job"])
        job.bookings.clear()
        db.session.commit()
    login(review_fixture["customer"])
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/done").status_code == 403


def test_mark_done_accepts_a_future_appointment_when_it_exists(client, login, review_fixture, app):
    with app.app_context():
        booking = db.session.scalar(db.select(Booking).where(Booking.job_id == review_fixture["job"]))
        booking.starts_at = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
        db.session.commit()
    login(review_fixture["customer"])
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/done").status_code == 302


def test_mark_done_rejects_multi_trade_job_with_missing_appointment(client, login, review_fixture, app):
    second_worker = None
    with app.app_context():
        second_worker = User(email="second-worker@example.com", name="Second Worker", role=ROLE_TRADESPERSON, verification_status=VERIFICATION_VERIFIED, email_verified=True)
        second_worker.set_password("correct-horse")
        db.session.add(second_worker)
        db.session.flush()
        job = db.session.get(Job, review_fixture["job"])
        second_trade = JobTrade(job_id=job.id, trade="Electrician")
        db.session.add(second_trade)
        db.session.flush()
        db.session.add(Quote(job_trade_id=second_trade.id, tradesperson_id=second_worker.id, price_cents=200, earliest_start=date.today(), status=QUOTE_ACCEPTED))
        db.session.commit()
    login(review_fixture["customer"])
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/done").status_code == 403


def test_booking_is_enough_to_review_without_accepted_quote(client, login, review_fixture, app):
    with app.app_context():
        quote = db.session.scalar(db.select(Quote).where(Quote.job_trade.has(job_id=review_fixture["job"])))
        quote.status = "pending"
        db.session.get(Job, review_fixture["job"]).status = JOB_DONE
        db.session.commit()
    login(review_fixture["customer"])
    assert client.post(
        f"/reviews/jobs/{review_fixture['job']}/tradespeople/{review_fixture['worker']}",
        data={"stars": "5"},
    ).status_code == 302


def test_review_requires_done_job_and_is_only_once(client, login, review_fixture, app):
    login(review_fixture["customer"])
    job = review_fixture["job"]
    assert client.post(f"/reviews/jobs/{job}/tradespeople/{review_fixture['worker']}", data={"stars": "5"}).status_code == 403
    client.post(f"/reviews/jobs/{job}/done")
    response = client.post(f"/reviews/jobs/{job}/tradespeople/{review_fixture['worker']}", data={"stars": "5", "comment": "Great work"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Your review was submitted." in response.data
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
