from datetime import date, datetime, time, timedelta

import pytest

from app.blueprints.bookings import now_local
from app.extensions import db
from app.models import (
    JOB_IN_PROGRESS,
    JOB_OPEN,
    QUOTE_ACCEPTED,
    QUOTE_PENDING,
    ROLE_ADMIN,
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
CARA = "cara@example.com"
OTHER = "other@example.com"

MONDAY = 0
TODAY = date.today()
# A Monday far enough ahead that it is never in the past, whatever day the tests run.
DAY = TODAY + timedelta(days=14 - TODAY.weekday())
MORNING = (time(9), time(12))


# --- Helpers ------------------------------------------------------------------------------


@pytest.fixture
def customer_id(make_user):
    return make_user(email=ANNA, email_verified=True)


@pytest.fixture
def make_tradesperson(app, make_user):
    def _make(email=BEN, slots=((MONDAY, MORNING),), trades=("Plumber", "Tiler")):
        user_id = make_user(email=email, role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED, email_verified=True)
        with app.app_context():
            db.session.add(
                TradespersonProfile(
                    user_id=user_id,
                    company_name=f"{email} GmbH",
                    service_area="Berlin",
                    trades=[TradespersonTrade(trade=trade, status=TRADE_APPROVED) for trade in trades],
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
def make_job(app):
    def _make(customer, trades=("Plumber",), is_small=False):
        with app.app_context():
            job = Job(
                customer_id=customer,
                title="Leaky tap",
                city="Berlin",
                description="Please fix it.",
                urgency="normal",
                is_small=is_small,
                trades=[JobTrade(trade=trade) for trade in trades],
            )
            db.session.add(job)
            db.session.commit()
            return job.id

    return _make


@pytest.fixture
def add_quote(app):
    def _add(job_id, tradesperson, trade="Plumber", start=DAY):
        with app.app_context():
            job_trade = db.session.scalar(db.select(JobTrade).filter_by(job_id=job_id, trade=trade))
            quote = Quote(job_trade_id=job_trade.id, tradesperson_id=tradesperson, price_cents=15000, earliest_start=start)
            db.session.add(quote)
            db.session.commit()
            return quote.id

    return _add


@pytest.fixture
def add_booking(app):
    def _add(job_id, tradesperson, starts_at):
        with app.app_context():
            booking = Booking(job_id=job_id, tradesperson_id=tradesperson, starts_at=starts_at)
            db.session.add(booking)
            db.session.get(Job, job_id).status = JOB_IN_PROGRESS
            db.session.commit()
            return booking.id

    return _add


def confirm(client, quote_id, day=DAY, at="09:00", **options):
    data = {"date": day.isoformat() if isinstance(day, date) else day, "time": at}
    return client.post(f"/appointments/quote/{quote_id}", data=data, **options)


def text_of(response):
    return response.get_data(as_text=True)


def quote_status(app, quote_id):
    with app.app_context():
        return db.session.get(Quote, quote_id).status


def bookings_of(app, job_id):
    with app.app_context():
        return list(db.session.scalars(db.select(Booking.starts_at).filter_by(job_id=job_id)))


def assert_refused(app, response, quote_id, job_id, message):
    assert message in text_of(response)
    assert quote_status(app, quote_id) == QUOTE_PENDING
    assert bookings_of(app, job_id) == []


@pytest.fixture
def quote(customer_id, make_tradesperson, make_job, add_quote, login):
    """A pending quote with Anna logged in. Returns (job id, quote id, tradesperson id)."""
    ben = make_tradesperson()
    job_id = make_job(customer_id)
    quote_id = add_quote(job_id, ben)
    login(ANNA)
    return job_id, quote_id, ben


# --- Accepting with an appointment --------------------------------------------------------


def test_accepting_stores_one_appointment(app, client, quote):
    job_id, quote_id, _ = quote

    confirm(client, quote_id, at="09:30")

    assert quote_status(app, quote_id) == QUOTE_ACCEPTED
    assert bookings_of(app, job_id) == [datetime.combine(DAY, time(9, 30))]
    with app.app_context():
        assert db.session.get(Job, job_id).status == JOB_IN_PROGRESS


def test_form_defaults_to_the_earliest_start_date(client, quote):
    _, quote_id, _ = quote

    assert f'value="{DAY.isoformat()}"' in text_of(client.get(f"/appointments/quote/{quote_id}"))


def test_form_never_defaults_to_a_past_date(client, customer_id, make_tradesperson, make_job, add_quote, login):
    quote_id = add_quote(make_job(customer_id), make_tradesperson(), start=TODAY - timedelta(days=30))
    login(ANNA)

    page = text_of(client.get(f"/appointments/quote/{quote_id}"))

    assert f'value="{now_local().date().isoformat()}"' in page


def test_each_trade_of_a_multi_trade_job_gets_its_own_appointment(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id, trades=("Plumber", "Tiler"))
    plumber = add_quote(job_id, make_tradesperson(), trade="Plumber")
    tiler = add_quote(job_id, make_tradesperson(email=CARA), trade="Tiler")
    login(ANNA)

    confirm(client, plumber)
    with app.app_context():
        assert db.session.get(Job, job_id).status == JOB_OPEN
    confirm(client, tiler)

    assert len(bookings_of(app, job_id)) == 2
    with app.app_context():
        assert db.session.get(Job, job_id).status == JOB_IN_PROGRESS


# --- Validation ---------------------------------------------------------------------------


def test_past_date_is_refused(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id)
    quote_id = add_quote(job_id, make_tradesperson(slots=()), start=TODAY - timedelta(days=30))
    login(ANNA)

    response = confirm(client, quote_id, day=TODAY - timedelta(days=1))

    assert_refused(app, response, quote_id, job_id, "cannot be in the past")


def test_date_before_the_earliest_start_is_refused(app, client, quote):
    job_id, quote_id, _ = quote

    response = confirm(client, quote_id, day=DAY - timedelta(days=7))

    assert_refused(app, response, quote_id, job_id, "before the earliest start date")


@pytest.mark.parametrize("day, at", [("", "09:00"), (DAY, ""), ("tomorrow", "09:00"), (DAY, "25:00")])
def test_missing_or_broken_date_and_time_are_refused(app, client, quote, day, at):
    job_id, quote_id, _ = quote

    response = confirm(client, quote_id, day=day, at=at)

    assert_refused(app, response, quote_id, job_id, "Please choose a date and a time.")


def test_refused_form_keeps_what_was_entered(client, quote):
    _, quote_id, _ = quote
    day = DAY + timedelta(days=1)

    page = text_of(confirm(client, quote_id, day=day, at="14:30"))

    assert f'value="{day.isoformat()}"' in page
    assert 'value="14:30"' in page


# --- Availability rule --------------------------------------------------------------------


@pytest.mark.parametrize("at", ["09:00", "10:00"])
def test_time_inside_a_slot_is_allowed(app, client, quote, at):
    _, quote_id, _ = quote

    confirm(client, quote_id, at=at)

    assert quote_status(app, quote_id) == QUOTE_ACCEPTED


@pytest.mark.parametrize(
    "day, at",
    [
        pytest.param(DAY, "08:00", id="before-the-slot"),
        pytest.param(DAY, "10:30", id="runs-past-its-end"),
        pytest.param(DAY, "13:00", id="after-the-slot"),
        pytest.param(DAY + timedelta(days=1), "09:00", id="other-weekday"),
    ],
)
def test_time_outside_the_slots_is_refused(app, client, quote, day, at):
    job_id, quote_id, _ = quote

    response = confirm(client, quote_id, day=day, at=at)

    assert_refused(app, response, quote_id, job_id, "outside the tradesperson&#39;s availability")


def test_without_slots_any_valid_time_is_allowed_with_a_note(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id)
    quote_id = add_quote(job_id, make_tradesperson(slots=()))
    login(ANNA)

    assert "still has to be confirmed" in text_of(client.get(f"/appointments/quote/{quote_id}"))
    response = confirm(client, quote_id, day=DAY + timedelta(days=5), at="19:00", follow_redirects=True)

    assert "still has to be confirmed" in text_of(response)
    assert bookings_of(app, job_id) == [datetime.combine(DAY + timedelta(days=5), time(19))]


def test_with_slots_no_note_is_shown(client, quote):
    _, quote_id, _ = quote

    assert "still has to be confirmed" not in text_of(client.get(f"/appointments/quote/{quote_id}"))


# --- No double-booking --------------------------------------------------------------------


@pytest.mark.parametrize("at", ["09:00", "10:00"])
def test_overlap_with_an_appointment_is_refused(app, client, quote, customer_id, make_job, add_booking, at):
    job_id, quote_id, ben = quote
    add_booking(make_job(customer_id), ben, datetime.combine(DAY, time(9)))

    response = confirm(client, quote_id, at=at)

    assert_refused(app, response, quote_id, job_id, "already has an appointment")


def test_overlap_with_a_booked_small_job_slot_is_refused(app, client, quote, customer_id, make_job, add_booking):
    job_id, quote_id, ben = quote
    add_booking(make_job(customer_id, is_small=True), ben, datetime.combine(DAY, time(9)))

    response = confirm(client, quote_id, at="10:00")

    assert_refused(app, response, quote_id, job_id, "already has an appointment")


def test_overlap_without_slots_is_refused_too(app, client, customer_id, make_tradesperson, make_job, add_quote, add_booking, login):
    ben = make_tradesperson(slots=())
    add_booking(make_job(customer_id, is_small=True), ben, datetime.combine(DAY, time(9)))
    job_id = make_job(customer_id)
    quote_id = add_quote(job_id, ben)
    login(ANNA)

    response = confirm(client, quote_id, at="10:59")

    assert_refused(app, response, quote_id, job_id, "already has an appointment")


def test_touching_appointments_do_not_overlap(app, client, customer_id, make_tradesperson, make_job, add_quote, add_booking, login):
    ben = make_tradesperson(slots=())
    add_booking(make_job(customer_id), ben, datetime.combine(DAY, time(9)))
    quote_id = add_quote(make_job(customer_id), ben)
    login(ANNA)

    confirm(client, quote_id, at="11:00")

    assert quote_status(app, quote_id) == QUOTE_ACCEPTED


def test_another_tradesperson_may_have_the_same_time(app, client, quote, customer_id, make_tradesperson, make_job, add_booking):
    _, quote_id, _ = quote
    add_booking(make_job(customer_id), make_tradesperson(email=CARA), datetime.combine(DAY, time(9)))

    confirm(client, quote_id)

    assert quote_status(app, quote_id) == QUOTE_ACCEPTED


def test_time_taken_at_the_last_moment_leaves_the_quote_pending(app, client, quote, customer_id, make_job, add_booking, monkeypatch):
    # Pretends the check ran just before another request won, so only the database can stop this one.
    job_id, quote_id, ben = quote
    add_booking(make_job(customer_id), ben, datetime.combine(DAY, time(9)))
    monkeypatch.setattr("app.blueprints.appointments.has_overlap", lambda *args: False)

    response = confirm(client, quote_id)

    assert_refused(app, response, quote_id, job_id, "just taken")


def test_small_job_slot_that_overlaps_an_appointment_is_not_offered(client, customer_id, make_tradesperson, make_job, add_booking, login):
    ben = make_tradesperson()
    add_booking(make_job(customer_id), ben, datetime.combine(DAY, time(10)))
    small_job = make_job(customer_id, is_small=True)
    login(ANNA)

    page = text_of(client.get(f"/bookings/job/{small_job}"))

    assert f'value="{DAY.isoformat()}"' not in page
    assert f'value="{(DAY + timedelta(days=7)).isoformat()}"' in page


def test_small_job_slot_that_overlaps_an_appointment_cannot_be_booked(app, client, customer_id, make_tradesperson, make_job, add_booking, login):
    ben = make_tradesperson()
    add_booking(make_job(customer_id), ben, datetime.combine(DAY, time(10)))
    small_job = make_job(customer_id, is_small=True)
    with app.app_context():
        slot_id = db.session.scalar(db.select(AvailabilitySlot.id))
    login(ANNA)

    client.post(f"/bookings/job/{small_job}/book", data={"slot_id": slot_id, "date": DAY.isoformat()})

    assert bookings_of(app, small_job) == []


def test_slot_with_an_appointment_inside_cannot_be_deleted(app, client, customer_id, make_tradesperson, make_job, add_booking, login):
    ben = make_tradesperson()
    add_booking(make_job(customer_id), ben, datetime.combine(DAY, time(10)))
    with app.app_context():
        slot_id = db.session.scalar(db.select(AvailabilitySlot.id))
    login(BEN)

    client.post(f"/bookings/availability/{slot_id}/delete")

    with app.app_context():
        assert db.session.get(AvailabilitySlot, slot_id) is not None


# --- Who sees the appointment -------------------------------------------------------------

SHOWN = DAY.strftime("%d %b %Y") + ", 09:00"


@pytest.fixture
def accepted(client, quote):
    job_id, quote_id, ben = quote
    confirm(client, quote_id)
    client.post("/auth/logout")
    return job_id


def test_customer_sees_the_appointment_everywhere(client, accepted, login):
    login(ANNA)

    for url in (f"/jobs/mine/{accepted}", "/jobs/mine", "/bookings/appointments"):
        assert SHOWN in text_of(client.get(url)), url


def test_accepted_tradesperson_sees_the_appointment(client, accepted, login):
    login(BEN)

    for url in (f"/jobs/{accepted}", "/bookings/appointments"):
        assert SHOWN in text_of(client.get(url)), url


def test_other_customer_does_not_see_the_appointment(client, accepted, make_user, login):
    make_user(email=OTHER, email_verified=True)
    login(OTHER)

    assert client.get(f"/jobs/mine/{accepted}").status_code == 403
    assert SHOWN not in text_of(client.get("/jobs/mine"))
    assert SHOWN not in text_of(client.get("/bookings/appointments"))


def test_other_tradesperson_does_not_see_the_appointment(client, accepted, make_tradesperson, login):
    make_tradesperson(email=CARA)
    login(CARA)

    assert client.get(f"/jobs/{accepted}").status_code == 404
    assert SHOWN not in text_of(client.get("/bookings/appointments"))


def test_tradesperson_on_a_multi_trade_job_only_sees_their_own_appointment(client, customer_id, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id, trades=("Plumber", "Tiler"))
    plumber = add_quote(job_id, make_tradesperson(), trade="Plumber")
    add_quote(job_id, make_tradesperson(email=CARA), trade="Tiler")
    login(ANNA)
    confirm(client, plumber)

    client.post("/auth/logout")
    login(CARA)
    page = client.get(f"/jobs/{job_id}")

    # Still open for the tiler, who has not been accepted yet.
    assert page.status_code == 200
    assert SHOWN not in text_of(page)


def test_appointment_from_a_quote_cannot_be_cancelled(app, client, accepted, login):
    login(ANNA)
    with app.app_context():
        booking_id = db.session.scalar(db.select(Booking.id))

    assert "Cancel booking" not in text_of(client.get("/bookings/appointments"))
    client.post(f"/bookings/{booking_id}/cancel")

    assert len(bookings_of(app, accepted)) == 1
    with app.app_context():
        assert db.session.get(Job, accepted).status == JOB_IN_PROGRESS


# --- Access -------------------------------------------------------------------------------


def test_crafted_post_cannot_accept_on_somebody_elses_job(app, client, customer_id, make_user, make_tradesperson, make_job, add_quote, login):
    job_id = make_job(customer_id)
    quote_id = add_quote(job_id, make_tradesperson())
    make_user(email=OTHER, email_verified=True)
    login(OTHER)

    assert confirm(client, quote_id).status_code == 403
    assert client.get(f"/appointments/quote/{quote_id}").status_code == 403
    assert quote_status(app, quote_id) == QUOTE_PENDING
    assert bookings_of(app, job_id) == []


@pytest.mark.parametrize("email", [BEN, "admin@example.com"])
def test_only_the_customer_can_accept(app, client, customer_id, make_user, make_tradesperson, make_job, add_quote, login, email):
    job_id = make_job(customer_id)
    quote_id = add_quote(job_id, make_tradesperson())
    make_user(email="admin@example.com", role=ROLE_ADMIN, email_verified=True)
    login(email)

    assert confirm(client, quote_id).status_code == 403
    assert bookings_of(app, job_id) == []


def test_anonymous_visitor_cannot_accept(app, client, customer_id, make_tradesperson, make_job, add_quote):
    job_id = make_job(customer_id)
    quote_id = add_quote(job_id, make_tradesperson())

    assert "/auth/login" in confirm(client, quote_id).location
    assert bookings_of(app, job_id) == []


def test_accepted_quote_cannot_get_a_second_appointment(app, client, quote):
    job_id, quote_id, _ = quote
    confirm(client, quote_id)

    response = confirm(client, quote_id, at="10:00", follow_redirects=True)

    assert "no longer be accepted" in text_of(response)
    assert len(bookings_of(app, job_id)) == 1


def test_accepting_checks_the_csrf_token(app, client, quote):
    job_id, quote_id, _ = quote
    app.config["WTF_CSRF_ENABLED"] = True

    assert confirm(client, quote_id).status_code == 400
    assert bookings_of(app, job_id) == []


def test_my_job_requires_login(client, customer_id, make_job):
    assert "/auth/login" in client.get(f"/jobs/mine/{make_job(customer_id)}").location
