from datetime import date, datetime, time, timedelta

import pytest

from app.blueprints.bookings import has_overlap
from app.extensions import db
from app.models import (
    QUOTE_PENDING,
    ROLE_TRADESPERSON,
    TRADE_APPROVED,
    VERIFICATION_VERIFIED,
    AvailabilitySlot,
    Booking,
    Job,
    JobTrade,
    Quote,
    TradespersonProfile,
    TradespersonTrade,
)

ANNA = "anna@example.com"
BEN = "ben@example.com"

TODAY = date.today()
# A Monday far enough ahead that it is never in the past, whatever day the tests run.
MONDAY_AHEAD = TODAY + timedelta(days=14 - TODAY.weekday())
MORNING = (time(9), time(12))  # a three hour slot


# --- Helpers ------------------------------------------------------------------------------


@pytest.fixture
def customer_id(make_user):
    return make_user(email=ANNA, email_verified=True)


@pytest.fixture
def make_tradesperson(app, make_user):
    def _make(slots=((0, MORNING),)):
        user_id = make_user(email=BEN, role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED, email_verified=True)
        with app.app_context():
            db.session.add(
                TradespersonProfile(
                    user_id=user_id,
                    company_name="Ben GmbH",
                    service_area="Berlin",
                    trades=[TradespersonTrade(trade="Plumber", status=TRADE_APPROVED)],
                    availability=[
                        AvailabilitySlot(weekday=weekday, start_time=start, end_time=end)
                        for weekday, (start, end) in slots
                    ],
                )
            )
            db.session.commit()
        return user_id

    return _make


@pytest.fixture
def job_id(app, customer_id):
    with app.app_context():
        job = Job(
            customer_id=customer_id,
            title="Leaky tap",
            city="Berlin",
            description="Please fix it.",
            urgency="normal",
            trades=[JobTrade(trade="Plumber")],
        )
        db.session.add(job)
        db.session.commit()
        return job.id


@pytest.fixture
def add_quote(app, job_id):
    def _add(tradesperson, minutes):
        with app.app_context():
            job_trade = db.session.scalar(db.select(JobTrade).filter_by(job_id=job_id))
            quote = Quote(
                job_trade_id=job_trade.id,
                tradesperson_id=tradesperson,
                price_cents=15000,
                earliest_start=MONDAY_AHEAD,
                duration_minutes=minutes,
                status=QUOTE_PENDING,
            )
            db.session.add(quote)
            db.session.commit()
            return quote.id

    return _add


def quote_data(**changes):
    data = {"price": "150.00", "earliest_start": MONDAY_AHEAD.isoformat(), "duration": "120", "message": ""}
    return {**data, **changes}


def send_quote(app, client, job_id, **changes):
    with app.app_context():
        job_trade_id = db.session.scalar(db.select(JobTrade.id).filter_by(job_id=job_id))
    return client.post(f"/quotes/job/{job_id}/new", data={"job_trade_id": job_trade_id, **quote_data(**changes)})


def quotes(app):
    with app.app_context():
        return db.session.scalars(db.select(Quote)).all()


def at(hour, minute=0):
    return datetime.combine(MONDAY_AHEAD, time(hour, minute))


def add_booking(app, job_id, tradesperson, starts_at, minutes):
    with app.app_context():
        db.session.add(
            Booking(job_id=job_id, tradesperson_id=tradesperson, starts_at=starts_at, duration_minutes=minutes)
        )
        db.session.commit()


def accept(client, quote_id, starts_at):
    return client.post(
        f"/appointments/quote/{quote_id}",
        data={"date": starts_at.date().isoformat(), "time": starts_at.strftime("%H:%M")},
    )


def bookings(app):
    with app.app_context():
        return db.session.scalars(db.select(Booking)).all()


# --- The quote form asks for the duration together with the price -------------------------


def test_new_quote_form_asks_for_a_duration_and_preselects_nothing(client, make_tradesperson, job_id, login):
    make_tradesperson()
    login(BEN)

    page = client.get(f"/quotes/job/{job_id}/new").get_data(as_text=True)

    assert 'name="duration"' in page
    assert "How long will the service take?" in page
    assert "Choose a duration" in page
    assert "selected" not in page  # the tradesperson has to pick one on purpose


def test_quote_with_a_duration_is_saved_with_it(app, client, make_tradesperson, job_id, login):
    make_tradesperson()
    login(BEN)

    response = send_quote(app, client, job_id, duration="150")

    assert response.status_code == 302
    assert [quote.duration_minutes for quote in quotes(app)] == [150]
    assert "takes 2.5 h" in client.get("/quotes/mine").get_data(as_text=True)


def test_quote_without_a_duration_is_not_saved_and_says_why(app, client, make_tradesperson, job_id, login):
    make_tradesperson()
    login(BEN)
    data = quote_data()
    del data["duration"]

    with app.app_context():
        job_trade_id = db.session.scalar(db.select(JobTrade.id).filter_by(job_id=job_id))
    page = client.post(f"/quotes/job/{job_id}/new", data={"job_trade_id": job_trade_id, **data}).get_data(as_text=True)

    assert quotes(app) == []
    assert "Please say how long the service will take." in page


@pytest.mark.parametrize("value", ["", "0", "-30", "45", "481", "abc", "1.5"])
def test_only_the_offered_durations_are_accepted(app, client, make_tradesperson, job_id, login, value):
    make_tradesperson()
    login(BEN)

    send_quote(app, client, job_id, duration=value)

    assert quotes(app) == []


def test_duration_must_fit_into_one_of_the_availability_slots(app, client, make_tradesperson, job_id, login):
    make_tradesperson()  # one slot of three hours
    login(BEN)

    page = send_quote(app, client, job_id, duration="210").get_data(as_text=True)

    assert quotes(app) == []
    assert "the longest is 3 h" in page
    assert send_quote(app, client, job_id, duration="180").status_code == 302


def test_tradesperson_without_slots_has_no_limit_yet(app, client, make_tradesperson, job_id, login):
    make_tradesperson(slots=())
    login(BEN)

    assert send_quote(app, client, job_id, duration="480").status_code == 302


def test_pending_quote_duration_can_be_corrected(app, client, make_tradesperson, add_quote, login):
    ben = make_tradesperson()
    quote_id = add_quote(ben, 120)
    login(BEN)

    client.post(f"/quotes/{quote_id}/edit", data=quote_data(duration="60"))
    assert [quote.duration_minutes for quote in quotes(app)] == [60]

    client.post(f"/quotes/{quote_id}/edit", data=quote_data(duration="45"))  # not an offered choice
    assert [quote.duration_minutes for quote in quotes(app)] == [60]


def test_edit_form_shows_the_current_duration(client, make_tradesperson, add_quote, login):
    ben = make_tradesperson()
    quote_id = add_quote(ben, 90)
    login(BEN)

    page = client.get(f"/quotes/{quote_id}/edit").get_data(as_text=True)

    assert '<option value="90" selected>' in page


@pytest.mark.parametrize("minutes, text", [(30, "30 min"), (60, "1 h"), (90, "1.5 h"), (120, "2 h"), (480, "8 h")])
def test_duration_is_shown_in_hours_and_minutes(app, minutes, text):
    assert app.jinja_env.filters["duration"](minutes) == text


# --- The appointment blocks exactly the stated time ---------------------------------------


def test_accepting_stores_the_quote_duration_on_the_appointment(
    app, client, make_tradesperson, add_quote, customer_id, login
):
    quote_id = add_quote(make_tradesperson(), 150)
    login(ANNA)

    assert accept(client, quote_id, at(9)).status_code == 302

    [booking] = bookings(app)
    assert booking.duration_minutes == 150
    assert booking.ends_at == at(11, 30)


def test_confirm_page_tells_how_long_the_appointment_takes(client, make_tradesperson, add_quote, customer_id, login):
    quote_id = add_quote(make_tradesperson(), 150)
    login(ANNA)

    page = client.get(f"/appointments/quote/{quote_id}").get_data(as_text=True)

    assert "this appointment takes 2.5 h" in page
    assert "two hours" not in page


def test_appointment_lists_show_start_and_end(app, client, make_tradesperson, add_quote, customer_id, login):
    quote_id = add_quote(make_tradesperson(), 150)
    login(ANNA)
    accept(client, quote_id, at(9))

    assert "09:00&ndash;11:30" in client.get("/bookings/appointments").get_data(as_text=True)  # as written in the HTML


def test_appointment_must_fit_the_slot_with_its_own_length(client, make_tradesperson, add_quote, customer_id, login):
    quote_id = add_quote(make_tradesperson(), 120)  # slot is 09:00-12:00
    login(ANNA)

    too_late = accept(client, quote_id, at(10, 30)).get_data(as_text=True)  # would end at 12:30
    assert "This time is outside" in too_late
    assert accept(client, quote_id, at(10)).status_code == 302  # ends exactly at 12:00


def test_a_long_appointment_blocks_the_time_after_its_start(
    app, client, make_tradesperson, add_quote, job_id, customer_id, login
):
    ben = make_tradesperson()
    add_booking(app, job_id, ben, at(9), 180)  # 09:00-12:00
    quote_id = add_quote(ben, 60)
    login(ANNA)

    # With the old fixed two hours the 11:00 hour would have been free.
    page = accept(client, quote_id, at(11)).get_data(as_text=True)

    assert "already has an appointment at this time" in page


def test_a_short_appointment_leaves_the_rest_free(app, make_tradesperson, job_id):
    ben = make_tradesperson()
    add_booking(app, job_id, ben, at(9), 60)  # 09:00-10:00

    with app.app_context():
        assert not has_overlap(ben, at(10), timedelta(minutes=60))  # touching is fine
        assert has_overlap(ben, at(9, 30), timedelta(minutes=60))


def test_a_new_long_appointment_cannot_swallow_a_later_booking(app, make_tradesperson, job_id):
    ben = make_tradesperson()
    add_booking(app, job_id, ben, at(11), 60)  # 11:00-12:00

    with app.app_context():
        assert has_overlap(ben, at(9), timedelta(minutes=180))
        assert not has_overlap(ben, at(9), timedelta(minutes=120))  # ends exactly when the other starts
