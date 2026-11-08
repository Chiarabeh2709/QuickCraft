from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import (
    Booking,
    JOB_DONE,
    JOB_IN_PROGRESS,
    JOB_OPEN,
    QUOTE_ACCEPTED,
    ROLE_ADMIN,
    ROLE_CUSTOMER,
    ROLE_TRADESPERSON,
    TRADE_APPROVED,
    TRADE_PENDING,
    TRADE_REJECTED,
    VERIFICATION_PENDING,
    VERIFICATION_REJECTED,
    VERIFICATION_VERIFIED,
    Job,
    JobTrade,
    Quote,
    Review,
    TradespersonProfile,
    TradespersonTrade,
    User,
)


@pytest.fixture
def review_fixture(app, make_user):
    customer = make_user(email="reviewer@example.com", email_verified=True)
    worker = make_user(email="worker@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED)
    other = make_user(email="other@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED)
    other_customer = make_user(email="other-customer@example.com", email_verified=True)
    with app.app_context():
        db.session.add(
            TradespersonProfile(
                user_id=worker,
                company_name="Worker GmbH",
                trades=[TradespersonTrade(trade="Plumber", status=TRADE_APPROVED)],
            )
        )
        job = Job(customer_id=customer, city="Lisbon", description="Fix sink", urgency="soon", status=JOB_OPEN)
        db.session.add(job)
        db.session.flush()
        trade = JobTrade(job_id=job.id, trade="Plumber")
        db.session.add(trade)
        db.session.flush()
        db.session.add(Quote(job_trade_id=trade.id, tradesperson_id=worker, price_cents=100, earliest_start=date.today(), status=QUOTE_ACCEPTED))
        db.session.add(Booking(job_id=job.id, tradesperson_id=worker, starts_at=datetime.combine(date.today(), datetime.min.time())))
        job.status = "in_progress"
        db.session.commit()
        return {"job": job.id, "customer": "reviewer@example.com", "other_customer": "other-customer@example.com", "worker": worker, "other": other}


def test_only_customer_can_mark_done(client, login, review_fixture):
    login(review_fixture["worker"] and "worker@example.com")
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/done").status_code == 403
    client.post("/auth/logout")
    login(review_fixture["customer"])
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/done").status_code == 302


@pytest.mark.parametrize("status", [JOB_OPEN, "cancelled", JOB_DONE])
def test_mark_done_rejects_non_in_progress_jobs(client, login, review_fixture, app, status):
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


def test_mark_done_does_not_require_appointment_to_have_passed(client, login, review_fixture, app):
    with app.app_context():
        booking = db.session.scalar(db.select(Booking).where(Booking.job_id == review_fixture["job"]))
        booking.starts_at = datetime.combine(date.today() + timedelta(days=30), datetime.min.time())
        db.session.commit()
    login(review_fixture["customer"])
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/done").status_code == 302


def test_mark_done_rejects_multi_trade_job_with_missing_appointment(client, login, review_fixture, app):
    with app.app_context():
        job = db.session.get(Job, review_fixture["job"])
        second_trade = JobTrade(job_id=job.id, trade="Electrician")
        db.session.add(second_trade)
        db.session.flush()
        db.session.add(
            Quote(
                job_trade_id=second_trade.id,
                tradesperson_id=review_fixture["other"],
                price_cents=200,
                earliest_start=date.today(),
                status=QUOTE_ACCEPTED,
            )
        )
        db.session.commit()
    login(review_fixture["customer"])
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/done").status_code == 403


def test_other_customer_cannot_mark_done(client, login, review_fixture):
    login(review_fixture["other_customer"])
    assert client.post(f"/reviews/jobs/{review_fixture['job']}/done").status_code == 403


def test_booked_tradesperson_can_be_reviewed_without_accepted_quote(client, login, review_fixture, app):
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
    search = client.get("/reviews/tradespeople?q=Worker")
    assert b"5.0" in profile.data and b"Great work" in profile.data
    assert b"5.0" in search.data and b"Worker GmbH" in search.data


# --- Who is listed on /reviews/tradespeople, and what it shows ------------------------------

LIST_URL = "/reviews/tradespeople"
CUSTOMER = "customer@example.com"
TRADESPERSON = "person@example.com"
COMPANY = "Listed Plumbing GmbH"
PERSONAL_NAME = "Private Person"
PHONE = "+49 40 5550777"
ADDRESS = "Werkstrasse 1, 20095 Hamburg"


@pytest.fixture
def make_listed(app, make_user):
    """Creates a tradesperson with private details and a profile, and returns the user id."""

    def _make(email=TRADESPERSON, status=VERIFICATION_VERIFIED, company=COMPANY, trades=None):
        user_id = make_user(email=email, role=ROLE_TRADESPERSON, status=status, email_verified=True)
        trades = {"Plumber": TRADE_APPROVED} if trades is None else trades
        with app.app_context():
            user = db.session.get(User, user_id)
            user.name, user.phone, user.address = PERSONAL_NAME, PHONE, ADDRESS
            db.session.add(
                TradespersonProfile(
                    user_id=user_id,
                    company_name=company,
                    qualification_reference="HWK-SECRET-1",
                    service_area="Berlin",
                    trades=[TradespersonTrade(trade=trade, status=value) for trade, value in trades.items()],
                )
            )
            db.session.commit()
        return user_id

    return _make


@pytest.fixture
def customer(make_user, login):
    make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=True)
    login(CUSTOMER)


def assert_no_private_data(page):
    for value in (PERSONAL_NAME, PHONE, ADDRESS, TRADESPERSON, "HWK-SECRET-1"):
        assert value not in page


# Who may open the list and the page of one tradesperson.


def urls(user_id):
    return [LIST_URL, f"{LIST_URL}/{user_id}"]


def test_anonymous_visitor_is_sent_to_login(client, make_listed):
    for url in urls(make_listed()):
        response = client.get(url)
        assert response.status_code == 302
        assert "/auth/login" in response.location


def test_unconfirmed_customer_is_sent_to_confirm_page(client, make_user, make_listed, login):
    user_id = make_listed()
    make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=False)
    login(CUSTOMER)
    for url in urls(user_id):
        response = client.get(url)
        assert response.status_code == 302
        assert response.location.endswith("/auth/confirm-email")


@pytest.mark.parametrize(
    "role, status",
    [(ROLE_TRADESPERSON, VERIFICATION_VERIFIED), (ROLE_TRADESPERSON, VERIFICATION_PENDING), (ROLE_ADMIN, None)],
)
def test_other_roles_cannot_open_it(client, make_user, make_listed, login, role, status):
    user_id = make_listed()
    make_user(email="user@example.com", role=role, status=status, email_verified=True)
    login("user@example.com")
    for url in urls(user_id):
        assert client.get(url).status_code == 403


def test_confirmed_customer_can_open_it(client, customer, make_listed):
    for url in urls(make_listed()):
        assert client.get(url).status_code == 200


# Who appears.


def test_list_shows_verified_tradespeople(client, customer, make_listed):
    make_listed()
    assert COMPANY in client.get(LIST_URL).get_data(as_text=True)


@pytest.mark.parametrize("status", [VERIFICATION_PENDING, VERIFICATION_REJECTED, None])
def test_unverified_tradesperson_is_never_listed(client, customer, make_listed, status):
    make_listed(status=status)
    assert COMPANY not in client.get(LIST_URL).get_data(as_text=True)
    assert COMPANY not in client.get(LIST_URL, query_string={"q": "Listed"}).get_data(as_text=True)


@pytest.mark.parametrize("status", [VERIFICATION_PENDING, VERIFICATION_REJECTED, None])
def test_unverified_tradesperson_cannot_be_reached_by_id(client, customer, make_listed, status):
    assert client.get(f"{LIST_URL}/{make_listed(status=status)}").status_code == 404


@pytest.mark.parametrize("status", [VERIFICATION_PENDING, VERIFICATION_REJECTED])
def test_unverified_tradesperson_cannot_see_own_page_by_id(client, make_listed, login, status):
    # They get the same answer as anyone not allowed here, which reveals nothing.
    user_id = make_listed(status=status)
    login(TRADESPERSON)
    assert client.get(f"{LIST_URL}/{user_id}").status_code == 403


@pytest.mark.parametrize("status", [TRADE_PENDING, TRADE_REJECTED])
def test_verified_tradesperson_without_approved_trade_cannot_be_reached_by_id(client, customer, make_listed, status):
    user_id = make_listed(trades={"Plumber": status})
    assert client.get(f"{LIST_URL}/{user_id}").status_code == 404


def test_verified_tradesperson_without_any_trade_cannot_be_reached_by_id(client, customer, make_listed):
    assert client.get(f"{LIST_URL}/{make_listed(trades={})}").status_code == 404


def test_customer_id_is_not_found(client, make_user, customer):
    other = make_user(email="other-customer@example.com", role=ROLE_CUSTOMER)
    assert client.get(f"{LIST_URL}/{other}").status_code == 404


def test_unknown_id_is_not_found(client, customer):
    assert client.get(f"{LIST_URL}/999").status_code == 404


def test_verified_user_without_profile_is_not_found(client, make_user, customer):
    user_id = make_user(email="no-profile@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED)
    assert client.get(f"{LIST_URL}/{user_id}").status_code == 404


def test_tradesperson_without_approved_trade_is_not_listed(client, customer, make_listed):
    make_listed(trades={"Plumber": TRADE_PENDING})
    assert COMPANY not in client.get(LIST_URL).get_data(as_text=True)


# What is shown.


def test_list_shows_company_and_approved_trades_only(client, customer, make_listed):
    make_listed(trades={"Plumber": TRADE_APPROVED, "Tiler": TRADE_PENDING, "Roofer": TRADE_REJECTED})
    page = client.get(LIST_URL).get_data(as_text=True)
    assert COMPANY in page and "Plumber" in page
    assert "Tiler" not in page and "Roofer" not in page


def test_page_shows_company_and_approved_trades_only(client, customer, make_listed):
    user_id = make_listed(trades={"Plumber": TRADE_APPROVED, "Tiler": TRADE_PENDING, "Roofer": TRADE_REJECTED})
    page = client.get(f"{LIST_URL}/{user_id}").get_data(as_text=True)
    assert COMPANY in page and "Plumber" in page
    assert "Tiler" not in page and "Roofer" not in page


def test_list_and_page_never_show_private_data(client, customer, make_listed):
    for url in urls(make_listed()):
        assert_no_private_data(client.get(url).get_data(as_text=True))


def test_search_finds_company_name(client, customer, make_listed):
    make_listed()
    make_listed(email="other@example.com", company="Tina Tiles")
    page = client.get(LIST_URL, query_string={"q": "listed"}).get_data(as_text=True)
    assert COMPANY in page
    assert "Tina Tiles" not in page


@pytest.mark.parametrize("term", [PERSONAL_NAME, "Private", PHONE, "Werkstrasse", TRADESPERSON])
def test_search_cannot_look_up_private_data(client, customer, make_listed, term):
    make_listed()
    assert COMPANY not in client.get(LIST_URL, query_string={"q": term}).get_data(as_text=True)


def test_search_treats_wildcards_as_plain_text(client, customer, make_listed):
    make_listed()
    assert COMPANY not in client.get(LIST_URL, query_string={"q": "L%d"}).get_data(as_text=True)


def test_search_term_is_escaped(client, customer):
    page = client.get(LIST_URL, query_string={"q": "<script>alert(1)</script>"}).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page


def test_rating_is_shown_for_a_reviewed_tradesperson(app, client, make_user, make_listed, login):
    user_id = make_listed()
    customer_id = make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=True)
    with app.app_context():
        job = Job(customer_id=customer_id, city="Berlin", description="Tap", urgency="normal", status="done")
        db.session.add(job)
        db.session.flush()
        db.session.add(Review(job_id=job.id, tradesperson_id=user_id, stars=4, comment="Good"))
        db.session.commit()
    login(CUSTOMER)
    assert "Rating: 4.0" in client.get(LIST_URL).get_data(as_text=True)
    page = client.get(f"{LIST_URL}/{user_id}").get_data(as_text=True)
    assert "4.0" in page and "Good" in page


# Where the customer lands after a review.


def test_review_sends_the_customer_back_to_the_job(client, login, review_fixture):
    login(review_fixture["customer"])
    job = review_fixture["job"]
    client.post(f"/reviews/jobs/{job}/done")
    response = client.post(f"/reviews/jobs/{job}/tradespeople/{review_fixture['worker']}", data={"stars": "5"})
    assert response.status_code == 302
    assert response.location.endswith(f"/reviews/jobs/{job}")


def test_review_works_for_a_tradesperson_who_has_no_approved_trade_anymore(app, client, login, review_fixture):
    # Their page is a 404 now, so the redirect must not lead there.
    with app.app_context():
        profile = db.session.scalar(db.select(TradespersonProfile).filter_by(user_id=review_fixture["worker"]))
        profile.trades[0].status = TRADE_PENDING
        db.session.commit()
    login(review_fixture["customer"])
    job = review_fixture["job"]
    client.post(f"/reviews/jobs/{job}/done")
    response = client.post(
        f"/reviews/jobs/{job}/tradespeople/{review_fixture['worker']}",
        data={"stars": "5"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert response.request.path == f"/reviews/jobs/{job}"
    with app.app_context():
        assert db.session.scalar(db.select(Review).where(Review.job_id == job)).stars == 5
