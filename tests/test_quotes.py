from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    JOB_CANCELLED,
    JOB_DONE,
    JOB_IN_PROGRESS,
    JOB_OPEN,
    QUOTE_ACCEPTED,
    QUOTE_PENDING,
    QUOTE_REJECTED,
    ROLE_ADMIN,
    ROLE_CUSTOMER,
    ROLE_TRADESPERSON,
    VERIFICATION_PENDING,
    VERIFICATION_REJECTED,
    VERIFICATION_VERIFIED,
    Booking,
    Job,
    JobTrade,
    Quote,
    TRADE_APPROVED,
    TradespersonProfile,
    TradespersonTrade,
    User,
)

PHONE = "+49 170 5550123"
ADDRESS = "Hauptstrasse 12, Berlin"
ANNA = "anna@example.com"
BEN = "ben@example.com"
CARA = "cara@example.com"

START = date.today() + timedelta(days=10)


# --- Helpers ------------------------------------------------------------------------------


@pytest.fixture
def customer_id(app, make_user):
    user_id = make_user(email=ANNA, email_verified=True)
    with app.app_context():
        user = db.session.get(User, user_id)
        user.phone = PHONE
        user.address = ADDRESS
        db.session.commit()
    return user_id


@pytest.fixture
def make_tradesperson(app, make_user):
    def _make(email=BEN, trades=("Plumber",), area="Berlin", status=VERIFICATION_VERIFIED):
        user_id = make_user(email=email, role=ROLE_TRADESPERSON, status=status, email_verified=True)
        with app.app_context():
            db.session.add(
                TradespersonProfile(
                    user_id=user_id,
                    company_name=f"{email} GmbH",
                    service_area=area,
                    trades=[TradespersonTrade(trade=trade, status=TRADE_APPROVED) for trade in trades],
                )
            )
            db.session.commit()
        return user_id

    return _make


@pytest.fixture
def make_job(app):
    def _make(customer, trades=("Plumber",), status=JOB_OPEN, city="Berlin", is_small=False):
        with app.app_context():
            job = Job(
                customer_id=customer,
                title="Leaky tap",
                city=city,
                description="Please fix it.",
                urgency="normal",
                status=status,
                is_small=is_small,
                trades=[JobTrade(trade=trade) for trade in trades],
            )
            db.session.add(job)
            db.session.commit()
            return job.id

    return _make


@pytest.fixture
def add_quote(app):
    def _add(job_id, tradesperson, trade="Plumber", status=QUOTE_PENDING, price_cents=15000, start=START):
        with app.app_context():
            job_trade = db.session.scalar(db.select(JobTrade).filter_by(job_id=job_id, trade=trade))
            quote = Quote(
                job_trade_id=job_trade.id,
                tradesperson_id=tradesperson,
                price_cents=price_cents,
                earliest_start=start,
                status=status,
            )
            db.session.add(quote)
            db.session.commit()
            return quote.id

    return _add


def job_trade_id(app, job_id, trade="Plumber"):
    with app.app_context():
        return db.session.scalar(db.select(JobTrade.id).filter_by(job_id=job_id, trade=trade))


def quote_form(app, job_id, trade="Plumber", **changes):
    form = {
        "job_trade_id": job_trade_id(app, job_id, trade),
        "price": "150.00",
        "earliest_start": START.isoformat(),
        "message": "I can do this.",
    }
    return {**form, **changes}


def accept(client, quote_id, **options):
    data = {"date": START.isoformat(), "time": "10:00"}
    return client.post(f"/appointments/quote/{quote_id}", data=data, **options)


def quote_count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Quote.id)))


def quote_status(app, quote_id):
    with app.app_context():
        return db.session.get(Quote, quote_id).status


def job_status(app, job_id):
    with app.app_context():
        return db.session.get(Job, job_id).status


def contact_visible(page):
    return PHONE in page or ADDRESS in page


def assert_sent_to_login(response):
    assert response.status_code == 302
    assert "/auth/login" in response.location


NOT_VERIFIED_TRADESPERSON = [
    (ROLE_CUSTOMER, None),
    (ROLE_ADMIN, None),
    (ROLE_TRADESPERSON, VERIFICATION_PENDING),
    (ROLE_TRADESPERSON, VERIFICATION_REJECTED),
]

NOT_CUSTOMER = [
    (ROLE_ADMIN, None),
    (ROLE_TRADESPERSON, VERIFICATION_VERIFIED),
    (ROLE_TRADESPERSON, VERIFICATION_PENDING),
]


# --- Sending a quote ----------------------------------------------------------------------


def test_tradesperson_can_send_a_quote(app, client, customer_id, make_tradesperson, make_job, login):
    ben = make_tradesperson()
    job_id = make_job(customer_id)
    login(BEN)

    client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id, price="149,50"))

    with app.app_context():
        quote = db.session.scalar(db.select(Quote))
        assert quote.tradesperson_id == ben
        assert quote.price_cents == 14950
        assert quote.earliest_start == START
        assert quote.message == "I can do this."
        assert quote.status == QUOTE_PENDING


def test_quote_form_loads_and_job_page_links_to_it(client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson()
    job_id = make_job(customer_id)
    login(BEN)

    assert f"/quotes/job/{job_id}/new" in client.get(f"/jobs/{job_id}").get_data(as_text=True)
    assert client.get(f"/quotes/job/{job_id}/new").status_code == 200


@pytest.mark.parametrize(
    "changes",
    [
        {"price": ""},
        {"price": "0"},
        {"price": "-5"},
        {"price": "cheap"},
        {"price": "NaN"},
        {"price": "Infinity"},
        {"price": "10.999"},
        {"price": "99999999"},
        {"earliest_start": ""},
        {"earliest_start": "soon"},
        {"earliest_start": (date.today() - timedelta(days=30)).isoformat()},
        {"message": "x" * 2001},
        {"job_trade_id": "999"},
    ],
)
def test_invalid_quote_is_not_saved(app, client, customer_id, make_tradesperson, make_job, login, changes):
    make_tradesperson()
    job_id = make_job(customer_id)
    login(BEN)

    client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id, **changes))

    assert quote_count(app) == 0


def test_message_is_optional(app, client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson()
    job_id = make_job(customer_id)
    login(BEN)

    client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id, message=""))

    assert quote_count(app) == 1


def test_second_quote_for_the_same_job_is_blocked(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    ben = make_tradesperson()
    job_id = make_job(customer_id)
    add_quote(job_id, ben)
    login(BEN)

    client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id))

    assert quote_count(app) == 1


def test_second_quote_for_another_trade_of_the_job_is_blocked(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    ben = make_tradesperson(trades=("Plumber", "Tiler"))
    job_id = make_job(customer_id, trades=("Plumber", "Tiler"))
    add_quote(job_id, ben, trade="Plumber")
    login(BEN)

    client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id, trade="Tiler"))

    assert quote_count(app) == 1


def test_database_itself_refuses_a_second_quote_for_a_trade(app, customer_id, make_tradesperson, make_job, add_quote):
    ben = make_tradesperson()
    job_id = make_job(customer_id)
    add_quote(job_id, ben)

    with pytest.raises(IntegrityError):
        add_quote(job_id, ben)


def test_double_submit_that_slips_past_the_check_is_caught(app, client, customer_id, make_tradesperson, make_job, add_quote, login, monkeypatch):
    # Pretends the first click is not visible yet, so only the database can stop the second one.
    monkeypatch.setattr("app.blueprints.quotes.has_quote_on", lambda job: False)
    ben = make_tradesperson()
    job_id = make_job(customer_id)
    add_quote(job_id, ben)
    login(BEN)

    response = client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id))

    assert response.status_code == 302
    assert quote_count(app) == 1


@pytest.mark.parametrize("status", [JOB_IN_PROGRESS, JOB_DONE, JOB_CANCELLED])
def test_only_open_jobs_can_get_quotes(app, client, customer_id, make_tradesperson, make_job, login, status):
    make_tradesperson()
    job_id = make_job(customer_id, status=status)
    login(BEN)

    assert client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id)).status_code == 404
    assert quote_count(app) == 0


@pytest.mark.parametrize("tradesperson", [{"trades": ("Painter",)}, {"area": "Hamburg"}])
def test_job_outside_trade_or_area_cannot_get_a_quote(app, client, customer_id, make_tradesperson, make_job, login, tradesperson):
    make_tradesperson(**tradesperson)
    job_id = make_job(customer_id)
    login(BEN)

    assert client.get(f"/quotes/job/{job_id}/new").status_code == 404
    assert client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id)).status_code == 404
    assert quote_count(app) == 0


def test_cannot_quote_for_a_trade_of_the_job_you_do_not_offer(app, client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson(trades=("Plumber",))
    job_id = make_job(customer_id, trades=("Plumber", "Tiler"))
    login(BEN)

    client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id, trade="Tiler"))

    assert quote_count(app) == 0


def test_unknown_job_is_not_found(client, make_tradesperson, login):
    make_tradesperson()
    login(BEN)

    assert client.get("/quotes/job/999/new").status_code == 404


def test_sending_a_quote_requires_login(app, client, customer_id, make_job):
    job_id = make_job(customer_id)

    assert_sent_to_login(client.get(f"/quotes/job/{job_id}/new"))
    assert_sent_to_login(client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id)))


@pytest.mark.parametrize("role, status", NOT_VERIFIED_TRADESPERSON)
def test_only_verified_tradespeople_can_send_quotes(app, client, customer_id, make_job, make_user, login, role, status):
    job_id = make_job(customer_id)
    make_user(email="someone@example.com", role=role, status=status, email_verified=True)
    login("someone@example.com")

    assert client.get(f"/quotes/job/{job_id}/new").status_code == 403
    assert client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id)).status_code == 403
    assert quote_count(app) == 0


# --- Editing and withdrawing --------------------------------------------------------------


def test_tradesperson_can_edit_own_pending_quote(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    quote_id = add_quote(make_job(customer_id), make_tradesperson())
    login(BEN)
    new_start = START + timedelta(days=3)

    client.post(
        f"/quotes/{quote_id}/edit",
        data={"price": "99.90", "earliest_start": new_start.isoformat(), "message": "Better offer."},
    )

    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        assert quote.price_cents == 9990
        assert quote.earliest_start == new_start
        assert quote.message == "Better offer."


def test_edit_form_shows_the_current_values(client, customer_id, make_tradesperson, make_job, add_quote, login):
    quote_id = add_quote(make_job(customer_id), make_tradesperson(), price_cents=14950)
    login(BEN)

    page = client.get(f"/quotes/{quote_id}/edit").get_data(as_text=True)

    assert "149.50" in page
    assert START.isoformat() in page


def test_invalid_edit_changes_nothing(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    quote_id = add_quote(make_job(customer_id), make_tradesperson())
    login(BEN)

    client.post(f"/quotes/{quote_id}/edit", data={"price": "0", "earliest_start": START.isoformat()})

    with app.app_context():
        assert db.session.get(Quote, quote_id).price_cents == 15000


def test_tradesperson_can_withdraw_and_quote_again(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    ben = make_tradesperson()
    job_id = make_job(customer_id)
    quote_id = add_quote(job_id, ben)
    login(BEN)

    client.post(f"/quotes/{quote_id}/withdraw")
    assert quote_count(app) == 0

    client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id))
    assert quote_count(app) == 1


@pytest.mark.parametrize("status", [QUOTE_ACCEPTED, QUOTE_REJECTED])
def test_only_pending_quotes_can_be_changed(app, client, customer_id, make_tradesperson, make_job, add_quote, login, status):
    quote_id = add_quote(make_job(customer_id), make_tradesperson(), status=status)
    login(BEN)

    client.post(f"/quotes/{quote_id}/edit", data={"price": "1", "earliest_start": START.isoformat()})
    client.post(f"/quotes/{quote_id}/withdraw")

    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        assert quote.price_cents == 15000
        assert quote.status == status


def test_tradesperson_cannot_touch_somebody_elses_quote(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    make_tradesperson()
    quote_id = add_quote(make_job(customer_id), make_tradesperson(email=CARA))
    login(BEN)

    assert client.get(f"/quotes/{quote_id}/edit").status_code == 403
    assert client.post(f"/quotes/{quote_id}/edit", data={"price": "1"}).status_code == 403
    assert client.post(f"/quotes/{quote_id}/withdraw").status_code == 403
    assert quote_count(app) == 1


def test_withdraw_needs_post(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    quote_id = add_quote(make_job(customer_id), make_tradesperson())
    login(BEN)

    assert client.get(f"/quotes/{quote_id}/withdraw").status_code == 405
    assert quote_count(app) == 1


def test_unknown_quote_is_not_found(client, make_tradesperson, login):
    make_tradesperson()
    login(BEN)

    assert client.get("/quotes/999/edit").status_code == 404
    assert client.post("/quotes/999/withdraw").status_code == 404


def test_editing_and_withdrawing_require_login(app, client, customer_id, make_tradesperson, make_job, add_quote):
    quote_id = add_quote(make_job(customer_id), make_tradesperson())

    assert_sent_to_login(client.get(f"/quotes/{quote_id}/edit"))
    assert_sent_to_login(client.post(f"/quotes/{quote_id}/withdraw"))
    assert quote_count(app) == 1


@pytest.mark.parametrize("role, status", NOT_VERIFIED_TRADESPERSON)
def test_only_verified_tradespeople_can_edit_and_withdraw(app, client, customer_id, make_tradesperson, make_job, add_quote, make_user, login, role, status):
    quote_id = add_quote(make_job(customer_id), make_tradesperson())
    make_user(email="someone@example.com", role=role, status=status, email_verified=True)
    login("someone@example.com")

    assert client.post(f"/quotes/{quote_id}/edit", data={"price": "1"}).status_code == 403
    assert client.post(f"/quotes/{quote_id}/withdraw").status_code == 403
    assert quote_count(app) == 1


# --- My quotes ----------------------------------------------------------------------------


@pytest.mark.parametrize("status", [QUOTE_PENDING, QUOTE_ACCEPTED, QUOTE_REJECTED])
def test_my_quotes_shows_the_status(client, customer_id, make_tradesperson, make_job, add_quote, login, status):
    add_quote(make_job(customer_id), make_tradesperson(), status=status)
    login(BEN)

    page = client.get("/quotes/mine").get_data(as_text=True)

    assert "Leaky tap" in page
    assert status.capitalize() in page


def test_tradesperson_never_sees_quotes_of_others(client, customer_id, make_tradesperson, make_job, add_quote, login):
    make_tradesperson()
    job_id = make_job(customer_id)
    add_quote(job_id, make_tradesperson(email=CARA), price_cents=12345)
    login(BEN)

    assert "123.45" not in client.get("/quotes/mine").get_data(as_text=True)
    assert "123.45" not in client.get(f"/jobs/{job_id}").get_data(as_text=True)
    assert client.get(f"/quotes/job/{job_id}").status_code == 403


def test_my_quotes_requires_login(client):
    assert_sent_to_login(client.get("/quotes/mine"))


@pytest.mark.parametrize("role, status", NOT_VERIFIED_TRADESPERSON)
def test_only_verified_tradespeople_have_my_quotes(client, make_user, login, role, status):
    make_user(email="someone@example.com", role=role, status=status, email_verified=True)
    login("someone@example.com")

    assert client.get("/quotes/mine").status_code == 403


# --- Customer: compare --------------------------------------------------------------------


@pytest.fixture
def two_quotes(customer_id, make_tradesperson, make_job, add_quote):
    """Ben is cheaper, Cara can start sooner."""
    job_id = make_job(customer_id)
    add_quote(job_id, make_tradesperson(), price_cents=10000, start=START + timedelta(days=5))
    add_quote(job_id, make_tradesperson(email=CARA), price_cents=20000, start=START)
    return job_id


def test_quotes_are_sorted_by_soonest_start_by_default(client, two_quotes, login):
    login(ANNA)

    page = client.get(f"/quotes/job/{two_quotes}").get_data(as_text=True)

    assert page.index(f"{CARA} GmbH") < page.index(f"{BEN} GmbH")


def test_quotes_can_be_sorted_by_price(client, two_quotes, login):
    login(ANNA)

    page = client.get(f"/quotes/job/{two_quotes}?sort=price").get_data(as_text=True)

    assert page.index(f"{BEN} GmbH") < page.index(f"{CARA} GmbH")


def test_multi_trade_quotes_are_grouped_by_trade(client, customer_id, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id, trades=("Plumber", "Tiler"))
    add_quote(job_id, make_tradesperson(), trade="Plumber")
    add_quote(job_id, make_tradesperson(email=CARA, trades=("Tiler",)), trade="Tiler")
    login(ANNA)

    page = client.get(f"/quotes/job/{job_id}").get_data(as_text=True)

    assert page.index("Plumber") < page.index(f"{BEN} GmbH") < page.index("Tiler") < page.index(f"{CARA} GmbH")


def test_my_jobs_links_to_the_quotes(client, customer_id, make_job, login):
    job_id = make_job(customer_id)
    login(ANNA)

    assert f"/quotes/job/{job_id}" in client.get("/jobs/mine").get_data(as_text=True)


def test_customer_cannot_see_quotes_of_somebody_elses_job(client, customer_id, make_user, make_job, login):
    job_id = make_job(customer_id)
    make_user(email="other@example.com", email_verified=True)
    login("other@example.com")

    assert client.get(f"/quotes/job/{job_id}").status_code == 403


def test_compare_requires_login(client, customer_id, make_job):
    assert_sent_to_login(client.get(f"/quotes/job/{make_job(customer_id)}"))


def test_unconfirmed_customer_cannot_compare(client, make_user, make_job, login):
    job_id = make_job(make_user(email="new@example.com"))
    login("new@example.com")

    assert "confirm" in client.get(f"/quotes/job/{job_id}").location


@pytest.mark.parametrize("role, status", NOT_CUSTOMER)
def test_only_customers_can_compare(client, customer_id, make_job, make_user, login, role, status):
    job_id = make_job(customer_id)
    make_user(email="someone@example.com", role=role, status=status, email_verified=True)
    login("someone@example.com")

    assert client.get(f"/quotes/job/{job_id}").status_code == 403


def test_compare_of_unknown_job_is_not_found(client, customer_id, login):
    login(ANNA)

    assert client.get("/quotes/job/999").status_code == 404


# --- Accepting: single trade --------------------------------------------------------------


def test_accepting_on_a_single_trade_job(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id)
    winner = add_quote(job_id, make_tradesperson())
    loser = add_quote(job_id, make_tradesperson(email=CARA))
    login(ANNA)

    accept(client, winner)

    assert quote_status(app, winner) == QUOTE_ACCEPTED
    assert quote_status(app, loser) == QUOTE_REJECTED
    assert job_status(app, job_id) == JOB_IN_PROGRESS


def test_quote_cannot_be_accepted_twice(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id)
    quote_id = add_quote(job_id, make_tradesperson())
    login(ANNA)

    accept(client, quote_id)
    response = accept(client, quote_id, follow_redirects=True)

    assert "no longer be accepted" in response.get_data(as_text=True)
    assert quote_status(app, quote_id) == QUOTE_ACCEPTED


def test_second_quote_of_a_trade_cannot_be_accepted_after_the_first(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id)
    first = add_quote(job_id, make_tradesperson())
    second = add_quote(job_id, make_tradesperson(email=CARA))
    login(ANNA)

    accept(client, first)
    accept(client, second)

    assert quote_status(app, first) == QUOTE_ACCEPTED
    assert quote_status(app, second) == QUOTE_REJECTED


def test_accept_that_lost_a_race_changes_nothing(app, client, customer_id, make_tradesperson, make_job, add_quote, login, monkeypatch):
    # Pretends the check ran just before another request won, so only the UPDATE can stop this one.
    monkeypatch.setattr("app.blueprints.appointments.can_accept", lambda quote: True)
    job_id = make_job(customer_id)
    quote_id = add_quote(job_id, make_tradesperson(), status=QUOTE_REJECTED)
    other = add_quote(job_id, make_tradesperson(email=CARA))
    login(ANNA)

    accept(client, quote_id)

    assert quote_status(app, quote_id) == QUOTE_REJECTED
    assert quote_status(app, other) == QUOTE_PENDING
    assert job_status(app, job_id) == JOB_OPEN


@pytest.mark.parametrize("status", [JOB_IN_PROGRESS, JOB_DONE, JOB_CANCELLED])
def test_nothing_can_be_accepted_on_a_closed_job(app, client, customer_id, make_tradesperson, make_job, add_quote, login, status):
    job_id = make_job(customer_id, status=status)
    quote_id = add_quote(job_id, make_tradesperson())
    login(ANNA)

    page = client.get(f"/quotes/job/{job_id}").get_data(as_text=True)
    accept(client, quote_id)

    assert f"/appointments/quote/{quote_id}" not in page
    assert quote_status(app, quote_id) == QUOTE_PENDING
    assert job_status(app, job_id) == status


def test_rejected_quote_cannot_be_accepted(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    quote_id = add_quote(make_job(customer_id), make_tradesperson(), status=QUOTE_REJECTED)
    login(ANNA)

    accept(client, quote_id)

    assert quote_status(app, quote_id) == QUOTE_REJECTED


def test_quote_of_a_no_longer_verified_tradesperson_cannot_be_accepted(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    ben = make_tradesperson()
    quote_id = add_quote(make_job(customer_id), ben)
    with app.app_context():
        db.session.get(User, ben).verification_status = VERIFICATION_REJECTED
        db.session.commit()
    login(ANNA)

    accept(client, quote_id)

    assert quote_status(app, quote_id) == QUOTE_PENDING


def test_customer_cannot_accept_on_somebody_elses_job(app, client, customer_id, make_user, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id)
    quote_id = add_quote(job_id, make_tradesperson())
    make_user(email="other@example.com", email_verified=True)
    login("other@example.com")

    assert accept(client, quote_id).status_code == 403
    assert quote_status(app, quote_id) == QUOTE_PENDING
    assert job_status(app, job_id) == JOB_OPEN


def test_tradesperson_cannot_accept_own_quote(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    quote_id = add_quote(make_job(customer_id), make_tradesperson())
    login(BEN)

    assert accept(client, quote_id).status_code == 403
    assert quote_status(app, quote_id) == QUOTE_PENDING


@pytest.mark.parametrize("role, status", NOT_CUSTOMER)
def test_only_customers_can_accept(app, client, customer_id, make_tradesperson, make_job, add_quote, make_user, login, role, status):
    quote_id = add_quote(make_job(customer_id), make_tradesperson())
    make_user(email="someone@example.com", role=role, status=status, email_verified=True)
    login("someone@example.com")

    assert accept(client, quote_id).status_code == 403
    assert quote_status(app, quote_id) == QUOTE_PENDING


def test_unconfirmed_customer_cannot_accept(app, client, make_user, make_tradesperson, make_job, add_quote, login):
    quote_id = add_quote(make_job(make_user(email="new@example.com")), make_tradesperson())
    login("new@example.com")

    assert "confirm" in accept(client, quote_id).location
    assert quote_status(app, quote_id) == QUOTE_PENDING


def test_anonymous_visitor_cannot_accept(app, client, customer_id, make_tradesperson, make_job, add_quote):
    quote_id = add_quote(make_job(customer_id), make_tradesperson())

    assert_sent_to_login(accept(client, quote_id))
    assert quote_status(app, quote_id) == QUOTE_PENDING


def test_opening_the_accept_form_changes_nothing(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    quote_id = add_quote(make_job(customer_id), make_tradesperson())
    login(ANNA)

    assert client.get(f"/appointments/quote/{quote_id}").status_code == 200
    assert quote_status(app, quote_id) == QUOTE_PENDING


def test_accept_unknown_quote_is_not_found(client, customer_id, login):
    login(ANNA)

    assert accept(client, 999).status_code == 404


# --- Accepting: multi trade ---------------------------------------------------------------


@pytest.fixture
def multi_trade(customer_id, make_tradesperson, make_job, add_quote):
    job_id = make_job(customer_id, trades=("Plumber", "Tiler"))
    plumber_a = add_quote(job_id, make_tradesperson(), trade="Plumber")
    plumber_b = add_quote(job_id, make_tradesperson(email=CARA), trade="Plumber")
    tiler = add_quote(job_id, make_tradesperson(email="tia@example.com", trades=("Tiler",)), trade="Tiler")
    return job_id, plumber_a, plumber_b, tiler


def test_accepting_only_rejects_quotes_of_the_same_trade(app, client, multi_trade, login):
    job_id, plumber_a, plumber_b, tiler = multi_trade
    login(ANNA)

    accept(client, plumber_a)

    assert quote_status(app, plumber_a) == QUOTE_ACCEPTED
    assert quote_status(app, plumber_b) == QUOTE_REJECTED
    assert quote_status(app, tiler) == QUOTE_PENDING


def test_multi_trade_job_stays_open_until_every_trade_is_filled(app, client, multi_trade, login):
    job_id, plumber_a, _, tiler = multi_trade
    login(ANNA)

    accept(client, plumber_a)
    assert job_status(app, job_id) == JOB_OPEN

    accept(client, tiler)
    assert job_status(app, job_id) == JOB_IN_PROGRESS


def test_nobody_can_quote_for_a_filled_trade(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id, trades=("Plumber", "Tiler"))
    add_quote(job_id, make_tradesperson(email=CARA), trade="Plumber", status=QUOTE_ACCEPTED)
    make_tradesperson()
    login(BEN)

    client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id))

    assert quote_count(app) == 1


def test_open_trade_of_a_partly_filled_job_can_still_get_quotes(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id, trades=("Plumber", "Tiler"))
    add_quote(job_id, make_tradesperson(email=CARA), trade="Plumber", status=QUOTE_ACCEPTED)
    make_tradesperson(trades=("Tiler",))
    login(BEN)

    client.post(f"/quotes/job/{job_id}/new", data=quote_form(app, job_id, trade="Tiler"))

    assert quote_count(app) == 2


def test_partly_filled_job_cannot_be_cancelled(app, client, multi_trade, login):
    job_id, plumber_a, _, _ = multi_trade
    login(ANNA)
    accept(client, plumber_a)

    client.post(f"/jobs/{job_id}/cancel")

    assert job_status(app, job_id) == JOB_OPEN


def test_partly_filled_small_job_cannot_be_booked(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id, trades=("Plumber", "Tiler"), is_small=True)
    add_quote(job_id, make_tradesperson(), trade="Plumber", status=QUOTE_ACCEPTED)
    login(ANNA)

    response = client.get(f"/bookings/job/{job_id}")

    assert response.status_code == 302
    assert "/jobs/mine" in response.location


# --- Bookings and quotes together ---------------------------------------------------------


def test_quote_cannot_be_accepted_while_the_job_is_booked(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    ben = make_tradesperson()
    job_id = make_job(customer_id, is_small=True)
    quote_id = add_quote(job_id, make_tradesperson(email=CARA))
    with app.app_context():
        db.session.add(Booking(job_id=job_id, tradesperson_id=ben, starts_at=datetime(2030, 1, 1, 9)))
        db.session.get(Job, job_id).status = JOB_IN_PROGRESS
        db.session.commit()
    login(ANNA)

    accept(client, quote_id)

    assert quote_status(app, quote_id) == QUOTE_PENDING


# --- Contact data privacy -----------------------------------------------------------------


def test_accepting_shows_contact_data_to_that_tradesperson_only(client, customer_id, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id)
    winner = add_quote(job_id, make_tradesperson())
    add_quote(job_id, make_tradesperson(email=CARA))

    login(BEN)
    assert not contact_visible(client.get(f"/jobs/{job_id}").get_data(as_text=True))

    client.post("/auth/logout")
    login(ANNA)
    accept(client, winner)

    client.post("/auth/logout")
    login(BEN)
    assert contact_visible(client.get(f"/jobs/{job_id}").get_data(as_text=True))

    client.post("/auth/logout")
    login(CARA)
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 404
    assert not contact_visible(response.get_data(as_text=True))


def test_multi_trade_contact_data_stays_hidden_from_tradespeople_still_pending(client, multi_trade, login):
    job_id, plumber_a, _, _ = multi_trade
    login(ANNA)
    accept(client, plumber_a)

    client.post("/auth/logout")
    login("tia@example.com")

    assert not contact_visible(client.get(f"/jobs/{job_id}").get_data(as_text=True))


def test_quote_pages_show_no_contact_data(client, customer_id, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id)
    add_quote(job_id, make_tradesperson(), status=QUOTE_ACCEPTED)
    login(BEN)

    assert not contact_visible(client.get("/quotes/mine").get_data(as_text=True))


# --- CSRF ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "email, url",
    [
        (BEN, "/quotes/job/{other_job}/new"),
        (BEN, "/quotes/{quote_id}/edit"),
        (BEN, "/quotes/{quote_id}/withdraw"),
        (ANNA, "/appointments/quote/{quote_id}"),
    ],
)
def test_every_form_checks_csrf_token(app, client, customer_id, make_tradesperson, make_job, add_quote, login, email, url):
    job_id = make_job(customer_id)
    other_job = make_job(customer_id)
    quote_id = add_quote(job_id, make_tradesperson())
    login(email)
    app.config["WTF_CSRF_ENABLED"] = True

    response = client.post(
        url.format(other_job=other_job, quote_id=quote_id),
        data=quote_form(app, other_job),
    )

    assert response.status_code == 400
    assert quote_count(app) == 1
    assert quote_status(app, quote_id) == QUOTE_PENDING
