"""Every action that changes a job's state, and the rule that must hold after each of them."""
from datetime import date, datetime, time, timedelta

import pytest

from app.blueprints.bookings import has_overlap
from app.blueprints.jobs import missing_appointments
from app.extensions import db
from app.models import (
    JOB_CANCELLED,
    JOB_DONE,
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
    User,
)

ANNA = "anna@example.com"
BEN = "ben@example.com"
CARA = "cara@example.com"
OTHER = "other@example.com"
PHONE = "+49 170 5550123"

MONDAY = 0
TODAY = date.today()
# A Monday far enough ahead that it is never in the past, whatever day the tests run.
DAY = TODAY + timedelta(days=14 - TODAY.weekday())
NINE = datetime.combine(DAY, time(9))
MORNING = (time(9), time(12))
NEEDED = "Appointment needed"


# --- Helpers ------------------------------------------------------------------------------


@pytest.fixture
def customer_id(app, make_user):
    user_id = make_user(email=ANNA, email_verified=True)
    with app.app_context():
        db.session.get(User, user_id).phone = PHONE
        db.session.commit()
    return user_id


@pytest.fixture
def make_tradesperson(app, make_user):
    def _make(email=BEN, slots=((MONDAY, MORNING),)):
        user_id = make_user(email=email, role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED, email_verified=True)
        with app.app_context():
            db.session.add(
                TradespersonProfile(
                    user_id=user_id,
                    company_name=f"{email} GmbH",
                    service_area="Berlin",
                    trades=[TradespersonTrade(trade=t, status=TRADE_APPROVED) for t in ("Plumber", "Tiler")],
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
    def _add(job_id, tradesperson, trade="Plumber"):
        with app.app_context():
            job_trade = db.session.scalar(db.select(JobTrade).filter_by(job_id=job_id, trade=trade))
            quote = Quote(job_trade_id=job_trade.id, tradesperson_id=tradesperson, price_cents=15000, earliest_start=DAY)
            db.session.add(quote)
            db.session.commit()
            return quote.id

    return _add


def post_time(client, url, day=DAY, at="09:00", **options):
    return client.post(url, data={"date": day.isoformat(), "time": at}, **options)


def accept(client, quote_id, **kwargs):
    return post_time(client, f"/appointments/quote/{quote_id}", **kwargs)


def rebook(client, quote_id, **kwargs):
    return post_time(client, f"/appointments/quote/{quote_id}/rebook", **kwargs)


def text_of(response):
    return response.get_data(as_text=True)


def job_status(app, job_id):
    with app.app_context():
        return db.session.get(Job, job_id).status


def quote_status(app, quote_id):
    with app.app_context():
        return db.session.get(Quote, quote_id).status


def booking_ids(app, job_id):
    with app.app_context():
        return list(db.session.scalars(db.select(Booking.id).filter_by(job_id=job_id)))


def starts_of(app, job_id):
    with app.app_context():
        return list(db.session.scalars(db.select(Booking.starts_at).filter_by(job_id=job_id)))


def assert_states_are_consistent(app):
    """The rule behind all of this. A partly filled multi-trade job is the one open job with an accepted quote."""
    with app.app_context():
        for job in db.session.scalars(db.select(Job)):
            filled = [any(q.status == QUOTE_ACCEPTED for q in job_trade.quotes) for job_trade in job.trades]
            if job.status == JOB_OPEN:
                assert not all(filled), f"open job {job.id} has every trade filled"
                # An open job has no booked slot: every appointment on it belongs to an accepted quote.
                accepted = {q.tradesperson_id for t in job.trades for q in t.quotes if q.status == QUOTE_ACCEPTED}
                assert {booking.tradesperson_id for booking in job.bookings} <= accepted
            if job.status == JOB_CANCELLED:
                assert not any(filled), f"cancelled job {job.id} has an accepted quote"


@pytest.fixture(autouse=True)
def invariant_after_every_test(app):
    # Runs after each test below, so every transition they make is checked.
    yield
    assert_states_are_consistent(app)


@pytest.fixture
def accepted(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    """A single-trade job with an accepted quote. Anna stays logged in. Returns (job, quote, booking)."""
    job_id = make_job(customer_id)
    quote_id = add_quote(job_id, make_tradesperson())
    login(ANNA)
    accept(client, quote_id)
    return job_id, quote_id, booking_ids(app, job_id)[0]


@pytest.fixture
def slot_booked(app, client, customer_id, make_tradesperson, make_job, login):
    """A small job with a booked slot and no quote. Anna stays logged in. Returns (job, booking)."""
    make_tradesperson()
    job_id = make_job(customer_id, is_small=True)
    with app.app_context():
        slot_id = db.session.scalar(db.select(AvailabilitySlot.id))
    login(ANNA)
    client.post(f"/bookings/job/{job_id}/book", data={"slot_id": slot_id, "date": DAY.isoformat()})
    return job_id, booking_ids(app, job_id)[0]


@pytest.fixture
def partly_filled(app, client, customer_id, make_tradesperson, make_job, add_quote, login):
    """A multi-trade job with only the plumber accepted. Returns (job, plumber quote, tiler quote, booking)."""
    job_id = make_job(customer_id, trades=("Plumber", "Tiler"))
    plumber = add_quote(job_id, make_tradesperson(), trade="Plumber")
    tiler = add_quote(job_id, make_tradesperson(email=CARA), trade="Tiler")
    login(ANNA)
    accept(client, plumber)
    return job_id, plumber, tiler, booking_ids(app, job_id)[0]


def switch_to(client, login, email):
    client.post("/auth/logout")
    login(email)


# --- Transitions: accepting and booking ---------------------------------------------------


def test_accepting_the_only_trade_starts_the_job(app, accepted):
    job_id, quote_id, _ = accepted

    assert job_status(app, job_id) == JOB_IN_PROGRESS
    assert quote_status(app, quote_id) == QUOTE_ACCEPTED


def test_accepting_one_of_two_trades_leaves_the_job_open(app, partly_filled):
    assert job_status(app, partly_filled[0]) == JOB_OPEN


def test_accepting_the_last_trade_starts_the_job(app, client, partly_filled):
    job_id, _, tiler, _ = partly_filled

    accept(client, tiler)

    assert job_status(app, job_id) == JOB_IN_PROGRESS


def test_booking_a_slot_starts_the_job(app, slot_booked):
    assert job_status(app, slot_booked[0]) == JOB_IN_PROGRESS


# --- Transitions: cancelling an appointment from a quote ----------------------------------


@pytest.mark.parametrize("email", [ANNA, BEN])
def test_cancelling_keeps_job_and_quote_and_frees_the_time(app, client, accepted, login, email):
    job_id, quote_id, booking_id = accepted
    switch_to(client, login, email)

    response = client.post(f"/bookings/{booking_id}/cancel", follow_redirects=True)

    assert "choose a new date and time" in text_of(response)
    assert job_status(app, job_id) == JOB_IN_PROGRESS
    assert quote_status(app, quote_id) == QUOTE_ACCEPTED
    assert booking_ids(app, job_id) == []
    with app.app_context():
        job = db.session.get(Job, job_id)
        assert [quote.id for quote in missing_appointments(job)] == [quote_id]
        assert not has_overlap(db.session.get(Quote, quote_id).tradesperson_id, NINE)


def test_cancelled_time_can_be_taken_by_another_job(app, client, accepted, customer_id, make_job, add_quote):
    _, quote_id, booking_id = accepted
    client.post(f"/bookings/{booking_id}/cancel")
    with app.app_context():
        ben = db.session.get(Quote, quote_id).tradesperson_id
    other_job = make_job(customer_id)

    accept(client, add_quote(other_job, ben))

    assert starts_of(app, other_job) == [NINE]


def test_both_sides_see_that_an_appointment_is_needed(client, accepted, login):
    job_id, _, booking_id = accepted
    client.post(f"/bookings/{booking_id}/cancel")

    for url in ("/jobs/mine", f"/jobs/mine/{job_id}"):
        assert NEEDED in text_of(client.get(url)), url

    switch_to(client, login, BEN)
    for url in (f"/jobs/{job_id}", "/quotes/mine"):
        assert NEEDED in text_of(client.get(url)), url


def test_nothing_says_appointment_needed_while_the_appointment_stands(client, accepted, login):
    job_id, _, _ = accepted

    assert NEEDED not in text_of(client.get("/jobs/mine"))
    switch_to(client, login, BEN)
    assert NEEDED not in text_of(client.get(f"/jobs/{job_id}"))


def test_contact_data_stays_visible_after_cancelling_an_appointment(client, accepted, login):
    job_id, _, booking_id = accepted
    client.post(f"/bookings/{booking_id}/cancel")

    switch_to(client, login, BEN)

    assert PHONE in text_of(client.get(f"/jobs/{job_id}"))


def test_job_waiting_for_an_appointment_cannot_be_cancelled(app, client, accepted):
    job_id, _, booking_id = accepted
    client.post(f"/bookings/{booking_id}/cancel")

    client.post(f"/jobs/{job_id}/cancel")

    assert job_status(app, job_id) == JOB_IN_PROGRESS


def test_cancelling_on_a_partly_filled_job_leaves_it_open_and_keeps_the_quote(app, client, partly_filled):
    job_id, plumber, tiler, booking_id = partly_filled

    client.post(f"/bookings/{booking_id}/cancel")

    assert job_status(app, job_id) == JOB_OPEN
    assert quote_status(app, plumber) == QUOTE_ACCEPTED
    assert quote_status(app, tiler) == QUOTE_PENDING


def test_multi_trade_job_only_misses_the_cancelled_appointment(app, client, partly_filled):
    job_id, plumber, tiler, booking_id = partly_filled
    accept(client, tiler)

    client.post(f"/bookings/{booking_id}/cancel")

    assert len(booking_ids(app, job_id)) == 1
    with app.app_context():
        assert [quote.id for quote in missing_appointments(db.session.get(Job, job_id))] == [plumber]


# --- Transitions: choosing a new appointment ----------------------------------------------


def take_nine_oclock(app, job_id, quote_id):
    """Gives the quote's tradesperson a booked slot on another job, at the time the tests ask for."""
    with app.app_context():
        ben = db.session.get(Quote, quote_id).tradesperson_id
        db.session.add(Booking(job_id=job_id, tradesperson_id=ben, starts_at=NINE))
        db.session.get(Job, job_id).status = JOB_IN_PROGRESS
        db.session.commit()


@pytest.fixture
def waiting(client, accepted):
    """An accepted quote whose appointment was cancelled. Returns (job, quote)."""
    job_id, quote_id, booking_id = accepted
    client.post(f"/bookings/{booking_id}/cancel")
    return job_id, quote_id


def test_customer_chooses_a_new_appointment(app, client, waiting):
    job_id, quote_id = waiting

    rebook(client, quote_id, at="10:00")

    assert starts_of(app, job_id) == [datetime.combine(DAY, time(10))]
    assert job_status(app, job_id) == JOB_IN_PROGRESS
    with app.app_context():
        assert missing_appointments(db.session.get(Job, job_id)) == []


def test_form_shows_the_tradespersons_slots(client, waiting):
    page = text_of(client.get(f"/appointments/quote/{waiting[1]}/rebook"))

    assert "Choose a new appointment" in page
    assert "Monday" in page


@pytest.mark.parametrize(
    "day, at, message",
    [
        pytest.param(TODAY - timedelta(days=1), "09:00", "cannot be in the past", id="past"),
        pytest.param(DAY - timedelta(days=7), "09:00", "before the earliest start", id="before-earliest-start"),
        pytest.param(DAY, "13:00", "outside the tradesperson", id="outside-availability"),
    ],
)
def test_new_appointment_has_the_same_validation(app, client, waiting, day, at, message):
    job_id, quote_id = waiting

    response = rebook(client, quote_id, day=day, at=at)

    assert message in text_of(response)
    assert booking_ids(app, job_id) == []


def test_new_appointment_cannot_overlap(app, client, waiting, customer_id, make_job):
    job_id, quote_id = waiting
    take_nine_oclock(app, make_job(customer_id), quote_id)

    response = rebook(client, quote_id, at="10:00")

    assert "already has an appointment" in text_of(response)
    assert 'value="10:00"' in text_of(response)
    assert booking_ids(app, job_id) == []


def test_time_taken_at_the_last_moment_is_refused(app, client, waiting, customer_id, make_job, monkeypatch):
    # Pretends the check ran just before another request won, so only the database can stop this one.
    job_id, quote_id = waiting
    take_nine_oclock(app, make_job(customer_id), quote_id)
    monkeypatch.setattr("app.blueprints.appointments.has_overlap", lambda *args: False)

    assert "just taken" in text_of(rebook(client, quote_id))
    assert booking_ids(app, job_id) == []


def test_appointment_that_still_stands_cannot_get_a_second_one(app, client, accepted):
    job_id, quote_id, _ = accepted

    response = rebook(client, quote_id, at="10:00", follow_redirects=True)

    assert "does not need a new appointment" in text_of(response)
    assert starts_of(app, job_id) == [NINE]


def test_pending_quote_cannot_use_the_new_appointment_route(app, client, partly_filled):
    job_id, _, tiler, _ = partly_filled

    rebook(client, tiler)

    assert quote_status(app, tiler) == QUOTE_PENDING
    assert len(booking_ids(app, job_id)) == 1


def test_finished_job_cannot_get_a_new_appointment(app, client, waiting):
    job_id, quote_id = waiting
    client.post(f"/reviews/jobs/{job_id}/done")

    rebook(client, quote_id)

    assert booking_ids(app, job_id) == []


# --- Transitions: cancelling a booked slot ------------------------------------------------


@pytest.mark.parametrize("email", [ANNA, BEN])
def test_cancelling_a_slot_reopens_the_job_and_frees_the_slot(app, client, slot_booked, login, email):
    job_id, booking_id = slot_booked
    switch_to(client, login, email)

    response = client.post(f"/bookings/{booking_id}/cancel", follow_redirects=True)

    assert "open again" in text_of(response)
    assert job_status(app, job_id) == JOB_OPEN
    assert booking_ids(app, job_id) == []
    with app.app_context():
        assert missing_appointments(db.session.get(Job, job_id)) == []


def test_cancelling_a_slot_hides_contact_data_again(client, slot_booked, login):
    job_id, booking_id = slot_booked
    client.post(f"/bookings/{booking_id}/cancel")

    switch_to(client, login, BEN)

    assert PHONE not in text_of(client.get(f"/jobs/{job_id}"))


def test_reopened_small_job_can_be_booked_again(app, client, slot_booked):
    job_id, booking_id = slot_booked
    client.post(f"/bookings/{booking_id}/cancel")
    with app.app_context():
        slot_id = db.session.scalar(db.select(AvailabilitySlot.id))

    client.post(f"/bookings/job/{job_id}/book", data={"slot_id": slot_id, "date": DAY.isoformat()})

    assert starts_of(app, job_id) == [NINE]
    assert job_status(app, job_id) == JOB_IN_PROGRESS


# --- Transitions: cancelling and finishing the job ----------------------------------------


def test_open_job_can_be_cancelled(app, client, customer_id, make_job, login):
    job_id = make_job(customer_id)
    login(ANNA)

    client.post(f"/jobs/{job_id}/cancel")

    assert job_status(app, job_id) == JOB_CANCELLED


def test_partly_filled_job_cannot_be_cancelled(app, client, partly_filled):
    job_id = partly_filled[0]

    client.post(f"/jobs/{job_id}/cancel")

    assert job_status(app, job_id) == JOB_OPEN


def test_running_job_can_be_marked_as_done(app, client, accepted):
    job_id = accepted[0]

    client.post(f"/reviews/jobs/{job_id}/done")

    assert job_status(app, job_id) == JOB_DONE


# --- Crafted requests ---------------------------------------------------------------------


@pytest.mark.parametrize("role", ["customer", "tradesperson", "admin"])
def test_outsiders_cannot_cancel_an_appointment(app, client, accepted, make_user, make_tradesperson, login, role):
    job_id, quote_id, booking_id = accepted
    if role == "customer":
        make_user(email=OTHER, email_verified=True)
    elif role == "tradesperson":
        make_tradesperson(email=OTHER)
    else:
        make_user(email=OTHER, role=ROLE_ADMIN, email_verified=True)
    switch_to(client, login, OTHER)

    assert client.post(f"/bookings/{booking_id}/cancel").status_code == 403
    assert booking_ids(app, job_id) == [booking_id]


def test_tradesperson_of_another_trade_cannot_cancel(app, client, partly_filled, login):
    job_id, plumber, tiler, booking_id = partly_filled
    accept(client, tiler)
    switch_to(client, login, CARA)

    assert client.post(f"/bookings/{booking_id}/cancel").status_code == 403
    assert booking_id in booking_ids(app, job_id)


def test_anonymous_visitor_cannot_cancel(app, client, accepted):
    job_id, _, booking_id = accepted
    client.post("/auth/logout")

    assert "/auth/login" in client.post(f"/bookings/{booking_id}/cancel").location
    assert booking_ids(app, job_id) == [booking_id]


def test_cancelling_twice_changes_nothing_more(app, client, accepted):
    job_id, quote_id, booking_id = accepted
    client.post(f"/bookings/{booking_id}/cancel")

    assert client.post(f"/bookings/{booking_id}/cancel").status_code == 404
    assert job_status(app, job_id) == JOB_IN_PROGRESS
    assert quote_status(app, quote_id) == QUOTE_ACCEPTED


def test_appointment_of_a_finished_job_cannot_be_cancelled(app, client, accepted):
    job_id, _, booking_id = accepted
    client.post(f"/reviews/jobs/{job_id}/done")

    response = client.post(f"/bookings/{booking_id}/cancel", follow_redirects=True)

    assert "Only upcoming appointments" in text_of(response)
    assert job_status(app, job_id) == JOB_DONE
    assert booking_ids(app, job_id) == [booking_id]


def test_past_appointment_cannot_be_cancelled(app, client, accepted):
    job_id, _, booking_id = accepted
    with app.app_context():
        db.session.get(Booking, booking_id).starts_at = datetime(2020, 1, 6, 9)
        db.session.commit()

    client.post(f"/bookings/{booking_id}/cancel")

    assert booking_ids(app, job_id) == [booking_id]


def test_cancel_needs_post(client, accepted):
    assert client.get(f"/bookings/{accepted[2]}/cancel").status_code == 405


def test_cancelling_checks_the_csrf_token(app, client, accepted):
    job_id, _, booking_id = accepted
    app.config["WTF_CSRF_ENABLED"] = True

    assert client.post(f"/bookings/{booking_id}/cancel").status_code == 400
    assert booking_ids(app, job_id) == [booking_id]


def test_new_appointment_checks_the_csrf_token(app, client, waiting):
    job_id, quote_id = waiting
    app.config["WTF_CSRF_ENABLED"] = True

    assert rebook(client, quote_id).status_code == 400
    assert booking_ids(app, job_id) == []


def test_other_customer_cannot_choose_a_new_appointment(app, client, waiting, make_user, login):
    job_id, quote_id = waiting
    make_user(email=OTHER, email_verified=True)
    switch_to(client, login, OTHER)

    assert client.get(f"/appointments/quote/{quote_id}/rebook").status_code == 403
    assert rebook(client, quote_id).status_code == 403
    assert booking_ids(app, job_id) == []


def test_tradesperson_cannot_choose_the_new_appointment(app, client, waiting, login):
    job_id, quote_id = waiting
    switch_to(client, login, BEN)

    assert rebook(client, quote_id).status_code == 403
    assert booking_ids(app, job_id) == []


def test_anonymous_visitor_cannot_choose_a_new_appointment(app, client, waiting):
    job_id, quote_id = waiting
    client.post("/auth/logout")

    assert "/auth/login" in rebook(client, quote_id).location
    assert booking_ids(app, job_id) == []


def test_new_appointment_for_an_unknown_quote_is_not_found(client, customer_id, login):
    login(ANNA)

    assert rebook(client, 999).status_code == 404
