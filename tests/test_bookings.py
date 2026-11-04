from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    JOB_CANCELLED,
    JOB_DONE,
    JOB_IN_PROGRESS,
    JOB_OPEN,
    ROLE_ADMIN,
    ROLE_CUSTOMER,
    ROLE_TRADESPERSON,
    VERIFICATION_PENDING,
    VERIFICATION_REJECTED,
    VERIFICATION_VERIFIED,
    AvailabilitySlot,
    Booking,
    Job,
    JobTrade,
    TRADE_APPROVED,
    TradespersonProfile,
    TradespersonTrade,
    User,
)

PHONE = "+49 170 5550123"
ADDRESS = "Hauptstrasse 12, Berlin"
ANNA = "anna@example.com"
BEN = "ben@example.com"

# Eight days ahead is always inside the booking window, whatever day the tests run.
FUTURE_DAY = date.today() + timedelta(days=8)
NINE = time(9)
NOON = time(12)


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
    def _make(customer, city="Berlin", trades=("Plumber",), status=JOB_OPEN, is_small=True):
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
def make_slot(app):
    def _make(tradesperson, weekday=FUTURE_DAY.weekday(), start=NINE, end=NOON):
        with app.app_context():
            profile = db.session.get(User, tradesperson).profile
            slot = AvailabilitySlot(profile=profile, weekday=weekday, start_time=start, end_time=end)
            db.session.add(slot)
            db.session.commit()
            return slot.id

    return _make


@pytest.fixture
def add_booking(app):
    def _add(job_id, tradesperson, starts_at=None, job_status=JOB_IN_PROGRESS):
        with app.app_context():
            booking = Booking(
                job_id=job_id,
                tradesperson_id=tradesperson,
                starts_at=starts_at or datetime.combine(FUTURE_DAY, NINE),
            )
            db.session.add(booking)
            db.session.get(Job, job_id).status = job_status
            db.session.commit()
            return booking.id

    return _add


def slot_form(weekday=0, start="09:00", end="12:00"):
    return {"weekday": weekday, "start_time": start, "end_time": end}


def book(client, job_id, slot_id, day=FUTURE_DAY):
    return client.post(f"/bookings/job/{job_id}/book", data={"slot_id": slot_id, "date": day.isoformat()})


def slot_count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(AvailabilitySlot.id)))


def booking_count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Booking.id)))


def job_status(app, job_id):
    with app.app_context():
        return db.session.get(Job, job_id).status


def contact_visible(page):
    return PHONE in page or ADDRESS in page


def assert_sent_to_login(response):
    assert response.status_code == 302
    assert "/auth/login" in response.location


# Everyone who is logged in but is not a verified tradesperson.
NOT_VERIFIED_TRADESPERSON = [
    (ROLE_CUSTOMER, None),
    (ROLE_ADMIN, None),
    (ROLE_TRADESPERSON, VERIFICATION_PENDING),
    (ROLE_TRADESPERSON, VERIFICATION_REJECTED),
]

# Everyone who is logged in but is not a customer.
NOT_CUSTOMER = [
    (ROLE_ADMIN, None),
    (ROLE_TRADESPERSON, VERIFICATION_VERIFIED),
    (ROLE_TRADESPERSON, VERIFICATION_PENDING),
]


# --- Availability: create and view --------------------------------------------------------


def test_tradesperson_can_add_and_see_a_slot(app, client, make_tradesperson, login):
    make_tradesperson()
    login(BEN)

    response = client.post("/bookings/availability", data=slot_form(weekday=2), follow_redirects=True)

    page = response.get_data(as_text=True)
    assert "Wednesday" in page and "09:00" in page and "12:00" in page
    assert slot_count(app) == 1


@pytest.mark.parametrize(
    "form",
    [
        slot_form(start="12:00", end="09:00"),  # end before start
        slot_form(start="09:00", end="09:00"),  # no length at all
        slot_form(start="", end="12:00"),
        slot_form(start="nine", end="12:00"),
        slot_form(weekday=7),
        slot_form(weekday="monday"),
    ],
)
def test_invalid_slot_is_not_saved(app, client, make_tradesperson, login, form):
    make_tradesperson()
    login(BEN)

    client.post("/bookings/availability", data=form)

    assert slot_count(app) == 0


@pytest.mark.parametrize(
    "start, end",
    [("10:00", "11:00"), ("08:00", "10:00"), ("11:00", "13:00"), ("08:00", "13:00"), ("09:00", "12:00")],
)
def test_overlapping_slot_is_rejected(app, client, make_tradesperson, make_slot, login, start, end):
    make_slot(make_tradesperson(), weekday=0)
    login(BEN)

    response = client.post("/bookings/availability", data=slot_form(start=start, end=end))

    assert "overlaps" in response.get_data(as_text=True)
    assert slot_count(app) == 1


def test_slot_may_touch_another_slot(app, client, make_tradesperson, make_slot, login):
    make_slot(make_tradesperson(), weekday=0)
    login(BEN)

    client.post("/bookings/availability", data=slot_form(start="12:00", end="15:00"))

    assert slot_count(app) == 2


def test_same_time_on_another_day_is_no_overlap(app, client, make_tradesperson, make_slot, login):
    make_slot(make_tradesperson(), weekday=0)
    login(BEN)

    client.post("/bookings/availability", data=slot_form(weekday=1))

    assert slot_count(app) == 2


def test_slots_of_other_tradespeople_do_not_count_as_overlap(app, client, make_tradesperson, make_slot, login):
    make_tradesperson()
    make_slot(make_tradesperson(email="other@example.com"), weekday=0)
    login(BEN)

    client.post("/bookings/availability", data=slot_form())

    assert slot_count(app) == 2


def test_tradesperson_only_sees_own_slots(client, make_tradesperson, make_slot, login):
    make_tradesperson()
    make_slot(make_tradesperson(email="other@example.com"), weekday=0)
    login(BEN)

    assert "Delete" not in client.get("/bookings/availability").get_data(as_text=True)


def test_availability_requires_login(client):
    assert_sent_to_login(client.get("/bookings/availability"))
    assert_sent_to_login(client.post("/bookings/availability", data=slot_form()))


@pytest.mark.parametrize("role, status", NOT_VERIFIED_TRADESPERSON)
def test_only_verified_tradespeople_can_manage_availability(app, client, make_user, login, role, status):
    make_user(email="someone@example.com", role=role, status=status, email_verified=True)
    login("someone@example.com")

    assert client.get("/bookings/availability").status_code == 403
    assert client.post("/bookings/availability", data=slot_form()).status_code == 403
    assert slot_count(app) == 0


# --- Availability: delete -----------------------------------------------------------------


def test_tradesperson_can_delete_own_slot(app, client, make_tradesperson, make_slot, login):
    slot_id = make_slot(make_tradesperson())
    login(BEN)

    client.post(f"/bookings/availability/{slot_id}/delete")

    assert slot_count(app) == 0


def test_tradesperson_cannot_delete_somebody_elses_slot(app, client, make_tradesperson, make_slot, login):
    make_tradesperson()
    slot_id = make_slot(make_tradesperson(email="other@example.com"))
    login(BEN)

    assert client.post(f"/bookings/availability/{slot_id}/delete").status_code == 403
    assert slot_count(app) == 1


def test_delete_needs_post(app, client, make_tradesperson, make_slot, login):
    slot_id = make_slot(make_tradesperson())
    login(BEN)

    assert client.get(f"/bookings/availability/{slot_id}/delete").status_code == 405
    assert slot_count(app) == 1


def test_delete_unknown_slot_is_not_found(client, make_tradesperson, login):
    make_tradesperson()
    login(BEN)

    assert client.post("/bookings/availability/999/delete").status_code == 404


def test_anonymous_visitor_cannot_delete_a_slot(app, client, make_tradesperson, make_slot):
    slot_id = make_slot(make_tradesperson())

    assert_sent_to_login(client.post(f"/bookings/availability/{slot_id}/delete"))
    assert slot_count(app) == 1


@pytest.mark.parametrize("role, status", NOT_VERIFIED_TRADESPERSON)
def test_only_verified_tradespeople_can_delete_slots(app, client, make_tradesperson, make_slot, make_user, login, role, status):
    slot_id = make_slot(make_tradesperson())
    make_user(email="someone@example.com", role=role, status=status, email_verified=True)
    login("someone@example.com")

    assert client.post(f"/bookings/availability/{slot_id}/delete").status_code == 403
    assert slot_count(app) == 1


def test_slot_with_upcoming_booking_cannot_be_deleted(
    app, client, customer_id, make_tradesperson, make_slot, make_job, add_booking, login
):
    ben = make_tradesperson()
    slot_id = make_slot(ben)
    add_booking(make_job(customer_id), ben)
    login(BEN)

    client.post(f"/bookings/availability/{slot_id}/delete")

    assert slot_count(app) == 1


def test_slot_with_only_past_bookings_can_be_deleted(
    app, client, customer_id, make_tradesperson, make_slot, make_job, add_booking, login
):
    ben = make_tradesperson()
    slot_id = make_slot(ben)
    past = datetime.combine(FUTURE_DAY - timedelta(weeks=4), NINE)
    add_booking(make_job(customer_id), ben, starts_at=past)
    login(BEN)

    client.post(f"/bookings/availability/{slot_id}/delete")

    assert slot_count(app) == 0
    assert booking_count(app) == 1


# --- Customer: see free slots -------------------------------------------------------------


def test_customer_sees_free_slots_of_a_matching_tradesperson(client, customer_id, make_tradesperson, make_slot, make_job, login):
    make_slot(make_tradesperson())
    job_id = make_job(customer_id)
    login(ANNA)

    page = client.get(f"/bookings/job/{job_id}").get_data(as_text=True)

    assert f"{BEN} GmbH" in page
    assert FUTURE_DAY.isoformat() in page


@pytest.mark.parametrize(
    "tradesperson",
    [
        {"trades": ("Painter",)},
        {"area": "Hamburg"},
        {"status": VERIFICATION_PENDING},
        {"status": VERIFICATION_REJECTED},
    ],
)
def test_slots_of_unsuitable_tradespeople_are_not_shown(client, customer_id, make_tradesperson, make_slot, make_job, login, tradesperson):
    make_slot(make_tradesperson(**tradesperson))
    job_id = make_job(customer_id)
    login(ANNA)

    assert FUTURE_DAY.isoformat() not in client.get(f"/bookings/job/{job_id}").get_data(as_text=True)


def test_booked_time_is_not_shown_to_anybody_else(client, make_user, customer_id, make_tradesperson, make_slot, make_job, add_booking, login):
    ben = make_tradesperson()
    make_slot(ben)
    add_booking(make_job(customer_id), ben)
    other = make_user(email="other@example.com", email_verified=True)
    other_job = make_job(other)
    login("other@example.com")

    page = client.get(f"/bookings/job/{other_job}").get_data(as_text=True)

    assert FUTURE_DAY.isoformat() not in page
    # Only that one week is blocked: the weekly slot stays free in the other weeks.
    assert (FUTURE_DAY + timedelta(weeks=1)).isoformat() in page


def test_past_times_are_not_shown(client, customer_id, make_tradesperson, make_slot, make_job, login, monkeypatch):
    # A Monday at 10:00, so this Monday's 09:00 slot has just passed.
    monkeypatch.setattr("app.blueprints.bookings.now_local", lambda: datetime(2030, 1, 7, 10))
    make_slot(make_tradesperson(), weekday=0)
    job_id = make_job(customer_id)
    login(ANNA)

    page = client.get(f"/bookings/job/{job_id}").get_data(as_text=True)

    assert "2030-01-07" not in page
    assert "2030-01-14" in page


def test_customer_cannot_see_slots_for_somebody_elses_job(client, customer_id, make_user, make_job, login):
    job_id = make_job(customer_id)
    make_user(email="other@example.com", email_verified=True)
    login("other@example.com")

    assert client.get(f"/bookings/job/{job_id}").status_code == 403


def test_slot_page_requires_login(client, customer_id, make_job):
    assert_sent_to_login(client.get(f"/bookings/job/{make_job(customer_id)}"))


def test_unconfirmed_customer_cannot_see_slots(client, make_user, make_job, login):
    job_id = make_job(make_user(email="new@example.com"))
    login("new@example.com")

    response = client.get(f"/bookings/job/{job_id}")

    assert response.status_code == 302
    assert "confirm" in response.location


@pytest.mark.parametrize("role, status", NOT_CUSTOMER)
def test_only_customers_can_see_slots(client, customer_id, make_job, make_user, login, role, status):
    job_id = make_job(customer_id)
    make_user(email="someone@example.com", role=role, status=status, email_verified=True)
    login("someone@example.com")

    assert client.get(f"/bookings/job/{job_id}").status_code == 403


# --- Customer: book -----------------------------------------------------------------------


def test_customer_can_book_a_free_slot(app, client, customer_id, make_tradesperson, make_slot, make_job, login):
    ben = make_tradesperson()
    slot_id = make_slot(ben)
    job_id = make_job(customer_id)
    login(ANNA)

    response = book(client, job_id, slot_id)

    assert response.status_code == 302
    assert job_status(app, job_id) == JOB_IN_PROGRESS
    with app.app_context():
        booking = db.session.scalar(db.select(Booking))
        assert booking.job_id == job_id
        assert booking.tradesperson_id == ben
        assert booking.starts_at == datetime.combine(FUTURE_DAY, NINE)


def test_second_customer_cannot_book_the_same_slot(app, client, make_user, customer_id, make_tradesperson, make_slot, make_job, add_booking, login):
    ben = make_tradesperson()
    slot_id = make_slot(ben)
    add_booking(make_job(customer_id), ben)
    other_job = make_job(make_user(email="other@example.com", email_verified=True))
    login("other@example.com")

    # This form was loaded while the slot was free, so only the database can stop it.
    response = book(client, other_job, slot_id)

    assert "somebody else just booked" in client.get(response.location).get_data(as_text=True)
    assert booking_count(app) == 1
    assert job_status(app, other_job) == JOB_OPEN


def test_database_itself_refuses_a_double_booking(app, customer_id, make_tradesperson, make_job):
    ben = make_tradesperson()
    first_job, second_job = make_job(customer_id), make_job(customer_id)
    starts_at = datetime.combine(FUTURE_DAY, NINE)

    with app.app_context():
        db.session.add(Booking(job_id=first_job, tradesperson_id=ben, starts_at=starts_at))
        db.session.add(Booking(job_id=second_job, tradesperson_id=ben, starts_at=starts_at))
        with pytest.raises(IntegrityError):
            db.session.commit()


def test_same_time_with_another_tradesperson_is_no_double_booking(app, client, customer_id, make_tradesperson, make_slot, make_job, add_booking, login):
    ben = make_tradesperson()
    add_booking(make_job(customer_id), ben)
    slot_id = make_slot(make_tradesperson(email="other@example.com"))
    job_id = make_job(customer_id)
    login(ANNA)

    book(client, job_id, slot_id)

    assert booking_count(app) == 2


def test_a_job_can_only_be_booked_once(app, client, customer_id, make_tradesperson, make_slot, make_job, login):
    ben = make_tradesperson()
    slot_id = make_slot(ben)
    later_slot = make_slot(ben, start=NOON, end=time(15))
    job_id = make_job(customer_id)
    login(ANNA)

    book(client, job_id, slot_id)
    book(client, job_id, later_slot)

    assert booking_count(app) == 1


def test_past_date_cannot_be_booked(app, client, customer_id, make_tradesperson, make_slot, make_job, login):
    slot_id = make_slot(make_tradesperson())
    job_id = make_job(customer_id)
    login(ANNA)

    book(client, job_id, slot_id, day=FUTURE_DAY - timedelta(weeks=4))

    assert booking_count(app) == 0
    assert job_status(app, job_id) == JOB_OPEN


def test_slot_that_started_earlier_today_cannot_be_booked(app, client, customer_id, make_tradesperson, make_slot, make_job, login, monkeypatch):
    monkeypatch.setattr("app.blueprints.bookings.now_local", lambda: datetime(2030, 1, 7, 10))
    slot_id = make_slot(make_tradesperson(), weekday=0)
    job_id = make_job(customer_id)
    login(ANNA)

    book(client, job_id, slot_id, day=date(2030, 1, 7))

    assert booking_count(app) == 0


@pytest.mark.parametrize(
    "day",
    [
        FUTURE_DAY + timedelta(days=1),  # not the slot's weekday
        FUTURE_DAY + timedelta(weeks=8),  # beyond the weeks we offer
    ],
)
def test_dates_the_slot_does_not_offer_cannot_be_booked(app, client, customer_id, make_tradesperson, make_slot, make_job, login, day):
    slot_id = make_slot(make_tradesperson())
    job_id = make_job(customer_id)
    login(ANNA)

    book(client, job_id, slot_id, day=day)

    assert booking_count(app) == 0


@pytest.mark.parametrize("form", [{}, {"slot_id": "999", "date": "2030-01-07"}, {"slot_id": "1", "date": "soon"}])
def test_broken_booking_form_books_nothing(app, client, customer_id, make_tradesperson, make_slot, make_job, login, form):
    make_slot(make_tradesperson())
    job_id = make_job(customer_id)
    login(ANNA)

    client.post(f"/bookings/job/{job_id}/book", data=form)

    assert booking_count(app) == 0


@pytest.mark.parametrize(
    "tradesperson",
    [
        {"trades": ("Painter",)},
        {"area": "Hamburg"},
        {"status": VERIFICATION_PENDING},
        {"status": VERIFICATION_REJECTED},
    ],
)
def test_unsuitable_tradesperson_cannot_be_booked(app, client, customer_id, make_tradesperson, make_slot, make_job, login, tradesperson):
    slot_id = make_slot(make_tradesperson(**tradesperson))
    job_id = make_job(customer_id)
    login(ANNA)

    book(client, job_id, slot_id)

    assert booking_count(app) == 0


@pytest.mark.parametrize(
    "job",
    [
        {"is_small": False},
        {"status": JOB_IN_PROGRESS},
        {"status": JOB_DONE},
        {"status": JOB_CANCELLED},
    ],
)
def test_only_open_small_jobs_can_be_booked(app, client, customer_id, make_tradesperson, make_slot, make_job, login, job):
    slot_id = make_slot(make_tradesperson())
    job_id = make_job(customer_id, **job)
    login(ANNA)

    book(client, job_id, slot_id)

    assert booking_count(app) == 0
    assert client.get(f"/bookings/job/{job_id}").status_code == 302


def test_customer_cannot_book_for_somebody_elses_job(app, client, customer_id, make_user, make_tradesperson, make_slot, make_job, login):
    slot_id = make_slot(make_tradesperson())
    job_id = make_job(customer_id)
    make_user(email="other@example.com", email_verified=True)
    login("other@example.com")

    assert book(client, job_id, slot_id).status_code == 403
    assert booking_count(app) == 0


def test_tradesperson_cannot_book_their_own_slot(app, client, customer_id, make_tradesperson, make_slot, make_job, login):
    slot_id = make_slot(make_tradesperson())
    job_id = make_job(customer_id)
    login(BEN)

    assert book(client, job_id, slot_id).status_code == 403
    assert booking_count(app) == 0


@pytest.mark.parametrize("role, status", NOT_CUSTOMER)
def test_only_customers_can_book(app, client, customer_id, make_tradesperson, make_slot, make_job, make_user, login, role, status):
    slot_id = make_slot(make_tradesperson())
    job_id = make_job(customer_id)
    make_user(email="someone@example.com", role=role, status=status, email_verified=True)
    login("someone@example.com")

    assert book(client, job_id, slot_id).status_code == 403
    assert booking_count(app) == 0


def test_unconfirmed_customer_cannot_book(app, client, make_user, make_tradesperson, make_slot, make_job, login):
    slot_id = make_slot(make_tradesperson())
    job_id = make_job(make_user(email="new@example.com"))
    login("new@example.com")

    assert "confirm" in book(client, job_id, slot_id).location
    assert booking_count(app) == 0


def test_anonymous_visitor_cannot_book(app, client, customer_id, make_tradesperson, make_slot, make_job):
    slot_id = make_slot(make_tradesperson())

    assert_sent_to_login(book(client, make_job(customer_id), slot_id))
    assert booking_count(app) == 0


def test_booking_needs_post(app, client, customer_id, make_tradesperson, make_slot, make_job, login):
    make_slot(make_tradesperson())
    job_id = make_job(customer_id)
    login(ANNA)

    assert client.get(f"/bookings/job/{job_id}/book").status_code == 405
    assert booking_count(app) == 0


# --- Customer: my bookings and cancelling -------------------------------------------------


def test_customer_only_sees_own_bookings(client, customer_id, make_user, make_tradesperson, make_job, add_booking, login):
    add_booking(make_job(customer_id), make_tradesperson())
    make_user(email="other@example.com", email_verified=True)

    login("other@example.com")
    assert "Leaky tap" not in client.get("/bookings/mine").get_data(as_text=True)

    client.post("/auth/logout")
    login(ANNA)
    assert "Leaky tap" in client.get("/bookings/mine").get_data(as_text=True)


def test_my_bookings_requires_login(client):
    assert_sent_to_login(client.get("/bookings/mine"))


@pytest.mark.parametrize("role, status", NOT_CUSTOMER)
def test_only_customers_have_my_bookings(client, make_user, login, role, status):
    make_user(email="someone@example.com", role=role, status=status, email_verified=True)
    login("someone@example.com")

    assert client.get("/bookings/mine").status_code == 403


def test_cancelling_frees_the_slot_and_reopens_the_job(app, client, customer_id, make_tradesperson, make_slot, make_job, add_booking, login):
    ben = make_tradesperson()
    make_slot(ben)
    job_id = make_job(customer_id)
    booking_id = add_booking(job_id, ben)
    login(ANNA)

    client.post(f"/bookings/{booking_id}/cancel")

    assert booking_count(app) == 0
    assert job_status(app, job_id) == JOB_OPEN
    assert FUTURE_DAY.isoformat() in client.get(f"/bookings/job/{job_id}").get_data(as_text=True)


def test_cancelled_slot_can_be_booked_again(app, client, customer_id, make_tradesperson, make_slot, make_job, add_booking, login):
    ben = make_tradesperson()
    slot_id = make_slot(ben)
    job_id = make_job(customer_id)
    booking_id = add_booking(job_id, ben)
    login(ANNA)

    client.post(f"/bookings/{booking_id}/cancel")
    book(client, job_id, slot_id)

    assert booking_count(app) == 1


def test_customer_cannot_cancel_somebody_elses_booking(app, client, customer_id, make_user, make_tradesperson, make_job, add_booking, login):
    booking_id = add_booking(make_job(customer_id), make_tradesperson())
    make_user(email="other@example.com", email_verified=True)
    login("other@example.com")

    assert client.post(f"/bookings/{booking_id}/cancel").status_code == 403
    assert booking_count(app) == 1


def test_past_booking_cannot_be_cancelled(app, client, customer_id, make_tradesperson, make_job, add_booking, login):
    past = datetime.combine(FUTURE_DAY - timedelta(weeks=4), NINE)
    booking_id = add_booking(make_job(customer_id), make_tradesperson(), starts_at=past)
    login(ANNA)

    client.post(f"/bookings/{booking_id}/cancel")

    assert booking_count(app) == 1


def test_booking_of_a_finished_job_cannot_be_cancelled(app, client, customer_id, make_tradesperson, make_job, add_booking, login):
    job_id = make_job(customer_id)
    booking_id = add_booking(job_id, make_tradesperson(), job_status=JOB_DONE)
    login(ANNA)

    client.post(f"/bookings/{booking_id}/cancel")

    assert booking_count(app) == 1
    assert job_status(app, job_id) == JOB_DONE


def test_cancel_needs_post(app, client, customer_id, make_tradesperson, make_job, add_booking, login):
    booking_id = add_booking(make_job(customer_id), make_tradesperson())
    login(ANNA)

    assert client.get(f"/bookings/{booking_id}/cancel").status_code == 405
    assert booking_count(app) == 1


def test_cancel_unknown_booking_is_not_found(client, customer_id, login):
    login(ANNA)

    assert client.post("/bookings/999/cancel").status_code == 404


def test_anonymous_visitor_cannot_cancel(app, client, customer_id, make_tradesperson, make_job, add_booking):
    booking_id = add_booking(make_job(customer_id), make_tradesperson())

    assert_sent_to_login(client.post(f"/bookings/{booking_id}/cancel"))
    assert booking_count(app) == 1


@pytest.mark.parametrize("role, status", NOT_CUSTOMER)
def test_only_customers_can_cancel(app, client, customer_id, make_tradesperson, make_job, add_booking, make_user, login, role, status):
    booking_id = add_booking(make_job(customer_id), make_tradesperson())
    make_user(email="someone@example.com", role=role, status=status, email_verified=True)
    login("someone@example.com")

    assert client.post(f"/bookings/{booking_id}/cancel").status_code == 403
    assert booking_count(app) == 1


def test_booked_tradesperson_cannot_cancel(app, client, customer_id, make_tradesperson, make_job, add_booking, login):
    booking_id = add_booking(make_job(customer_id), make_tradesperson())
    login(BEN)

    assert client.post(f"/bookings/{booking_id}/cancel").status_code == 403
    assert booking_count(app) == 1


# --- Tradesperson: appointments -----------------------------------------------------------


def test_tradesperson_sees_own_upcoming_appointments(client, customer_id, make_tradesperson, make_job, add_booking, login):
    ben = make_tradesperson()
    job_id = make_job(customer_id)
    add_booking(job_id, ben)
    login(BEN)

    page = client.get("/bookings/appointments").get_data(as_text=True)

    assert "Leaky tap" in page
    assert f"/jobs/{job_id}" in page
    # Contact data is only ever shown on the job page.
    assert not contact_visible(page)


def test_tradesperson_does_not_see_appointments_of_others(client, customer_id, make_tradesperson, make_job, add_booking, login):
    make_tradesperson()
    add_booking(make_job(customer_id), make_tradesperson(email="other@example.com"))
    login(BEN)

    assert "Leaky tap" not in client.get("/bookings/appointments").get_data(as_text=True)


def test_appointments_require_login(client):
    assert_sent_to_login(client.get("/bookings/appointments"))


@pytest.mark.parametrize("role, status", NOT_VERIFIED_TRADESPERSON)
def test_only_verified_tradespeople_have_appointments(client, make_user, login, role, status):
    make_user(email="someone@example.com", role=role, status=status, email_verified=True)
    login("someone@example.com")

    assert client.get("/bookings/appointments").status_code == 403


# --- Contact data privacy -----------------------------------------------------------------


def test_booking_shows_contact_data_to_that_tradesperson(client, customer_id, make_tradesperson, make_slot, make_job, login):
    slot_id = make_slot(make_tradesperson())
    job_id = make_job(customer_id)

    login(BEN)
    assert not contact_visible(client.get(f"/jobs/{job_id}").get_data(as_text=True))

    client.post("/auth/logout")
    login(ANNA)
    book(client, job_id, slot_id)

    client.post("/auth/logout")
    login(BEN)
    assert contact_visible(client.get(f"/jobs/{job_id}").get_data(as_text=True))


def test_booking_does_not_show_contact_data_to_other_tradespeople(client, customer_id, make_tradesperson, make_job, add_booking, login):
    job_id = make_job(customer_id)
    add_booking(job_id, make_tradesperson())
    make_tradesperson(email="other@example.com")
    login("other@example.com")

    response = client.get(f"/jobs/{job_id}")

    assert response.status_code == 404
    assert not contact_visible(response.get_data(as_text=True))


def test_cancelling_hides_contact_data_again(client, customer_id, make_tradesperson, make_job, add_booking, login):
    job_id = make_job(customer_id)
    booking_id = add_booking(job_id, make_tradesperson())
    login(ANNA)
    client.post(f"/bookings/{booking_id}/cancel")

    client.post("/auth/logout")
    login(BEN)

    assert not contact_visible(client.get(f"/jobs/{job_id}").get_data(as_text=True))


def test_slot_page_shows_no_contact_data_of_the_customer(client, customer_id, make_tradesperson, make_slot, make_job, login):
    make_slot(make_tradesperson())
    job_id = make_job(customer_id)
    login(ANNA)

    assert not contact_visible(client.get(f"/bookings/job/{job_id}").get_data(as_text=True))


# --- CSRF ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "email, url",
    [
        (BEN, "/bookings/availability"),
        (BEN, "/bookings/availability/{slot_id}/delete"),
        (ANNA, "/bookings/job/{job_id}/book"),
        (ANNA, "/bookings/{booking_id}/cancel"),
    ],
)
def test_every_form_checks_csrf_token(app, client, customer_id, make_tradesperson, make_slot, make_job, add_booking, login, email, url):
    ben = make_tradesperson()
    slot_id = make_slot(ben)
    job_id = make_job(customer_id)
    booking_id = add_booking(make_job(customer_id), ben)
    login(email)
    app.config["WTF_CSRF_ENABLED"] = True

    response = client.post(url.format(slot_id=slot_id, job_id=job_id, booking_id=booking_id), data=slot_form())

    assert response.status_code == 400
    assert slot_count(app) == 1
    assert booking_count(app) == 1


# --- Marking a job as small ---------------------------------------------------------------


@pytest.mark.parametrize("form, expected", [({"small": "1"}, True), ({}, False)])
def test_customer_can_mark_a_job_as_small(app, client, customer_id, login, form, expected):
    login(ANNA)

    client.post(
        "/jobs/new",
        data={"title": "Tap", "description": "It drips.", "city": "Berlin", "trades": ["Plumber"], **form},
    )

    with app.app_context():
        assert db.session.scalar(db.select(Job)).is_small is expected


def test_book_button_only_appears_on_open_small_jobs(client, customer_id, make_job, login):
    small_job = make_job(customer_id)
    big_job = make_job(customer_id, is_small=False)
    login(ANNA)

    page = client.get("/jobs/mine").get_data(as_text=True)

    assert f"/bookings/job/{small_job}" in page
    assert f"/bookings/job/{big_job}" not in page
