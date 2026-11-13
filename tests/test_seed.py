import re
import time
from datetime import date, datetime

import pytest

import config
from app import create_app
from app.blueprints.auth import EMAIL_PATTERN, phone_is_valid
from app.blueprints.bookings import now_local, upcoming_starts
from app.extensions import db
from app.models import (
    JOB_CANCELLED,
    JOB_DONE,
    JOB_IN_PROGRESS,
    JOB_OPEN,
    QUOTE_ACCEPTED,
    ROLE_ADMIN,
    ROLE_CUSTOMER,
    ROLE_TRADESPERSON,
    TRADE_APPROVED,
    TRADE_PENDING,
    TRADES,
    VERIFICATION_PENDING,
    VERIFICATION_VERIFIED,
    AvailabilitySlot,
    Booking,
    Job,
    JobTrade,
    Message,
    MessageThread,
    Project,
    ProjectComment,
    ProjectSchedule,
    Quote,
    Review,
    TradespersonProfile,
    TradespersonTrade,
    User,
    find_user_by_email,
)
from app.seed import CUSTOMERS, DEMO_PASSWORD, JOBS, QUOTES, TRADESPEOPLE, seed_demo_data, seed_if_enabled
from config import TestConfig

ALL_MODELS = (
    User,
    TradespersonProfile,
    TradespersonTrade,
    AvailabilitySlot,
    Job,
    JobTrade,
    Quote,
    Booking,
    Project,
    ProjectSchedule,
    ProjectComment,
    Review,
    MessageThread,
    Message,
)
CITIES = {"Berlin", "Hamburg", "Munich", "Cologne"}
VERIFIED = [key for key, data in TRADESPEOPLE.items() if data.get("status") is None]


def counts(app):
    with app.app_context():
        return {model.__name__: db.session.query(model).count() for model in ALL_MODELS}


@pytest.fixture
def seeded(app):
    with app.app_context():
        assert seed_demo_data() is True
    return app


def log_in(client, email):
    client.post("/auth/logout")
    response = client.post("/auth/login", data={"email": email, "password": DEMO_PASSWORD})
    assert response.status_code == 302
    assert "/auth/login" not in response.location
    return response


def page(client, url, **params):
    return client.get(url, query_string=params).get_data(as_text=True)


def customer_email(key="emma"):
    return CUSTOMERS[key][1]


def all_seed_text():
    texts = []
    for name, email, phone, address in CUSTOMERS.values():
        texts += [name, address]
    for data in TRADESPEOPLE.values():
        texts += [data["name"], data["company"], data["area"], data["address"], data["description"]]
    for data in JOBS.values():
        texts += [data["title"], data["city"], data["description"]]
    texts += [quote[-1] for quote in QUOTES]
    return texts


# --- The seed runs, and only once ---------------------------------------------------------


def test_seed_runs_on_an_empty_database(seeded):
    assert counts(seeded) == {
        "User": 13,
        "TradespersonProfile": 9,
        "TradespersonTrade": 11,
        "AvailabilitySlot": 18,
        "Job": 12,
        "JobTrade": 16,
        "Quote": 8,
        "Booking": 6,
        "Project": 1,
        "ProjectSchedule": 2,
        "ProjectComment": 4,
        "Review": 1,
        "MessageThread": 1,
        "Message": 3,
    }


def test_running_it_twice_gives_the_same_counts(seeded):
    before = counts(seeded)
    with seeded.app_context():
        assert seed_demo_data() is False
    assert counts(seeded) == before


def test_seed_runs_when_only_the_admin_exists(app, make_user):
    make_user(email="boss@example.com", role=ROLE_ADMIN)
    with app.app_context():
        assert seed_demo_data() is True
    assert counts(app)["User"] == 14


@pytest.mark.parametrize("role", [ROLE_CUSTOMER, ROLE_TRADESPERSON])
def test_seed_never_touches_a_database_with_other_users(app, make_user, role):
    make_user(email="real@example.com", role=role)
    with app.app_context():
        assert seed_demo_data() is False
    assert counts(app)["User"] == 1
    assert counts(app)["Job"] == 0


def test_seed_is_fast(app):
    start = time.perf_counter()
    with app.app_context():
        seed_demo_data()
    assert time.perf_counter() - start < 3


# --- Opt-in -------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["true", "TRUE", " true "])
def test_env_flag_is_on_for_true(monkeypatch, value):
    monkeypatch.setenv("SEED_DEMO_DATA", value)
    assert config.env_flag("SEED_DEMO_DATA") is True


@pytest.mark.parametrize("value", ["", "false", "1", "yes", "on"])
def test_env_flag_is_off_for_anything_else(monkeypatch, value):
    monkeypatch.setenv("SEED_DEMO_DATA", value)
    assert config.env_flag("SEED_DEMO_DATA") is False


def test_env_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SEED_DEMO_DATA", raising=False)
    assert config.env_flag("SEED_DEMO_DATA") is False


def test_app_start_does_not_seed_by_default(app):
    assert counts(app)["User"] == 0
    with app.app_context():
        assert seed_if_enabled() is False
    assert counts(app)["User"] == 0


def seed_on_config(**settings):
    return type("SeedOn", (TestConfig,), {"SEED_DEMO_DATA": True, **settings})


def test_app_start_seeds_when_switched_on():
    assert counts(create_app(seed_on_config()))["Job"] == 12


def test_app_start_keeps_the_admin_from_the_environment_apart():
    app = create_app(seed_on_config(ADMIN_EMAIL="boss@example.com", ADMIN_PASSWORD="admin-only-secret"))
    with app.app_context():
        admin = find_user_by_email("boss@example.com")
        assert admin.role == ROLE_ADMIN
        assert admin.check_password("admin-only-secret")
        assert not admin.check_password(DEMO_PASSWORD)
    assert counts(app)["User"] == 14


def test_reset_db_seeds_when_switched_on(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    app = create_app(seed_on_config())
    result = app.test_cli_runner().invoke(args=["reset-db"])
    assert result.exit_code == 0
    assert "Demo data created" in result.output
    assert counts(app)["Job"] == 12


def test_reset_db_leaves_the_database_empty_when_switched_off(app, monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    result = app.test_cli_runner().invoke(args=["reset-db"])
    assert result.exit_code == 0
    assert "Demo data created" not in result.output
    assert counts(app)["User"] == 0


def test_reset_db_on_render_still_refuses_and_keeps_the_seed(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    app = create_app(seed_on_config())
    monkeypatch.setenv("RENDER", "true")
    result = app.test_cli_runner().invoke(args=["reset-db"])
    assert result.exit_code != 0
    assert counts(app)["Job"] == 12


# --- A failure stops nothing and leaves nothing behind ------------------------------------


def test_failure_leaves_no_half_data_and_does_not_crash(app, monkeypatch):
    def explode(*args):
        raise RuntimeError("something is missing")

    monkeypatch.setattr("app.seed.create_review", explode)
    with app.app_context():
        assert seed_demo_data() is False
    assert all(count == 0 for count in counts(app).values())


def test_app_still_starts_when_the_seed_fails(monkeypatch):
    def explode(*args):
        raise RuntimeError("something is missing")

    monkeypatch.setattr("app.seed.create_demo_data", explode)
    app = create_app(seed_on_config())
    assert app.test_client().get("/").status_code == 200


# --- The accounts -------------------------------------------------------------------------


@pytest.mark.parametrize("key", CUSTOMERS)
def test_demo_customers_can_log_in_and_are_confirmed(seeded, client, key):
    log_in(client, customer_email(key))
    assert client.get("/jobs/mine").status_code == 200


@pytest.mark.parametrize("key", TRADESPEOPLE)
def test_demo_tradespeople_can_log_in(seeded, client, key):
    log_in(client, TRADESPEOPLE[key]["email"])


def test_account_numbers_and_statuses(seeded):
    with seeded.app_context():
        people = db.session.scalars(db.select(User).filter_by(role=ROLE_TRADESPERSON)).all()
        customers = db.session.scalars(db.select(User).filter_by(role=ROLE_CUSTOMER)).all()
        assert len(customers) == 4
        assert all(customer.email_verified for customer in customers)
        assert sum(p.verification_status == VERIFICATION_VERIFIED for p in people) == 8
        assert sum(p.verification_status == VERIFICATION_PENDING for p in people) == 1
        assert not db.session.query(User).filter_by(role=ROLE_ADMIN).count()


def test_required_trades_are_covered_by_fully_approved_tradespeople():
    fully_approved = [
        trade
        for key, data in TRADESPEOPLE.items()
        if set(data["trades"].values()) == {TRADE_APPROVED}
        for trade in data["trades"]
    ]
    assert {"Plumber", "Electrician", "Painter", "Tiler", "Carpenter", "Heating engineer"} <= set(fully_approved)
    assert len([key for key, data in TRADESPEOPLE.items() if set(data["trades"].values()) == {TRADE_APPROVED}]) == 7


def test_seed_uses_only_real_trades():
    used = {trade for data in TRADESPEOPLE.values() for trade in data["trades"]}
    used |= {trade for data in JOBS.values() for trade in data["trades"]}
    assert used <= set(TRADES)


def test_verified_tradespeople_have_everything_the_profile_needs():
    for key in VERIFIED:
        data = TRADESPEOPLE[key]
        assert data["company"] and data["reference"] and data["description"] and data["slots"]
        assert data["trades"]


def test_verified_tradespeople_serve_several_cities():
    for key in VERIFIED:
        assert "," in TRADESPEOPLE[key]["area"]


# --- Invented, English data ---------------------------------------------------------------


def test_all_contact_data_passes_the_signup_rules():
    people = [(c[1], c[2], c[3]) for c in CUSTOMERS.values()]
    people += [(d["email"], d["phone"], d["address"]) for d in TRADESPEOPLE.values()]
    for email, phone, address in people:
        assert EMAIL_PATTERN.fullmatch(email)
        assert phone_is_valid(phone)
        assert re.fullmatch(r"[^,]+, \d{5} (Berlin|Hamburg|Munich|Cologne)", address)


def test_emails_use_the_reserved_example_domain():
    emails = [c[1] for c in CUSTOMERS.values()] + [d["email"] for d in TRADESPEOPLE.values()]
    assert all(email.endswith("@example.com") for email in emails)
    assert len(set(emails)) == len(emails)


def test_phone_numbers_come_from_the_blocks_reserved_for_fiction():
    phones = [c[2] for c in CUSTOMERS.values()] + [d["phone"] for d in TRADESPEOPLE.values()]
    assert all(re.fullmatch(r"\+49 (30 23125|40 66969|89 99998|221 4710) \d{3}", phone) for phone in phones)
    assert len(set(phones)) == len(phones)


def test_cities_use_one_english_spelling_everywhere():
    assert {data["city"] for data in JOBS.values()} <= CITIES
    for data in TRADESPEOPLE.values():
        assert {city.strip() for city in data["area"].split(",")} <= CITIES


def test_no_german_letters_or_words_anywhere():
    german = re.compile(
        r"[äöüÄÖÜß]|\b(münchen|köln|muenchen|koeln|strasse|straße|weg|platz|gmbh|und|herr|frau|handwerker)\b",
        re.IGNORECASE,
    )
    for text in all_seed_text():
        assert not german.search(text), text


def test_demo_guide_is_english_and_warns(seeded):
    with open("DEMO.md", encoding="utf-8") as file:
        guide = file.read()
    assert not re.search(r"[äöüÄÖÜß]", guide)
    assert "fake" in guide and DEMO_PASSWORD in guide
    for data in TRADESPEOPLE.values():
        assert data["email"] in guide and data["company"] in guide
    for _, email, _, _ in CUSTOMERS.values():
        assert email in guide


def test_ids_in_the_demo_guide_are_right(seeded):
    with seeded.app_context():
        assert db.session.get(Job, 4).title == JOBS["boiler"]["title"]
        assert db.session.get(Job, 5).title == JOBS["tap"]["title"]
        assert db.session.get(Job, 11).title == JOBS["bedroom"]["title"]
        assert db.session.get(Project, 1).job.title == JOBS["kitchen"]["title"]


# --- Search -------------------------------------------------------------------------------


@pytest.mark.parametrize("key", VERIFIED)
def test_verified_tradespeople_appear_in_search(seeded, client, key):
    log_in(client, customer_email())
    assert TRADESPEOPLE[key]["company"] in page(client, "/profiles/")


@pytest.mark.parametrize("key", VERIFIED)
def test_each_approved_trade_and_city_finds_the_tradesperson(seeded, client, key):
    log_in(client, customer_email())
    data = TRADESPEOPLE[key]
    for trade, status in data["trades"].items():
        if status != TRADE_APPROVED:
            continue
        for city in data["area"].split(", "):
            assert data["company"] in page(client, "/profiles/", trade=trade, city=city)


def test_pending_trade_does_not_find_the_tradesperson(seeded, client):
    log_in(client, customer_email())
    company = TRADESPEOPLE["roofing"]["company"]
    assert company in page(client, "/profiles/", trade="Roofer")
    assert company not in page(client, "/profiles/", trade="Carpenter")


def test_pending_account_is_not_in_search(seeded, client):
    log_in(client, customer_email())
    assert TRADESPEOPLE["newstart"]["company"] not in page(client, "/profiles/")
    assert TRADESPEOPLE["newstart"]["company"] not in page(client, "/reviews/tradespeople")


def test_search_never_shows_private_data(seeded, client):
    log_in(client, customer_email())
    results = page(client, "/profiles/")
    for data in TRADESPEOPLE.values():
        assert data["phone"] not in results
        assert data["address"] not in results


# --- Jobs ---------------------------------------------------------------------------------


@pytest.mark.parametrize("key", VERIFIED)
def test_verified_tradespeople_see_matching_jobs(seeded, client, key):
    log_in(client, TRADESPEOPLE[key]["email"])
    data = TRADESPEOPLE[key]
    approved = {trade for trade, status in data["trades"].items() if status == TRADE_APPROVED}
    cities = set(data["area"].split(", "))
    expected = [
        job["title"]
        for job in JOBS.values()
        if job.get("status", JOB_OPEN) == JOB_OPEN and job["city"] in cities and approved & set(job["trades"])
    ]
    assert expected, f"{key} would have no job to look at in the demo"
    jobs_page = page(client, "/jobs/")
    assert all(title in jobs_page for title in expected)


def test_pending_trade_gives_no_jobs(seeded, client):
    with seeded.app_context():
        shelves_id = db.session.scalar(db.select(Job.id).filter_by(title=JOBS["shelves"]["title"]))
    log_in(client, TRADESPEOPLE["roofing"]["email"])
    assert JOBS["shelves"]["title"] not in page(client, "/jobs/")
    assert client.get(f"/jobs/{shelves_id}").status_code == 404
    log_in(client, TRADESPEOPLE["oak"]["email"])
    assert client.get(f"/jobs/{shelves_id}").status_code == 200


def test_pending_account_sees_no_jobs(seeded, client):
    log_in(client, TRADESPEOPLE["newstart"]["email"])
    assert client.get("/jobs/").status_code == 403


def test_jobs_are_in_the_promised_states(seeded):
    with seeded.app_context():
        jobs = db.session.scalars(db.select(Job)).all()
        by_status = {status: [job for job in jobs if job.status == status] for status in {job.status for job in jobs}}
        assert len(by_status[JOB_OPEN]) == 6
        assert len(by_status[JOB_IN_PROGRESS]) == 4
        assert len(by_status[JOB_DONE]) == 1
        assert len(by_status[JOB_CANCELLED]) == 1
        assert sum(job.urgency == "urgent" for job in by_status[JOB_OPEN]) == 2
        assert any(len(job.trades) > 1 for job in by_status[JOB_OPEN])
        assert any(job.is_small for job in by_status[JOB_OPEN])


def test_job_with_two_quotes_from_two_tradespeople(seeded, client):
    log_in(client, customer_email("thomas"))
    compare = page(client, "/quotes/job/4")
    assert TRADESPEOPLE["warm"]["company"] in compare
    assert TRADESPEOPLE["allround"]["company"] in compare
    assert compare.count("Accept") >= 2


def test_every_quote_comes_from_an_approved_trade_in_the_right_city():
    for job_key, trade, person, *_ in QUOTES:
        data = TRADESPEOPLE[person]
        assert data["trades"][trade] == TRADE_APPROVED
        assert JOBS[job_key]["city"] in data["area"]
        assert trade in JOBS[job_key]["trades"]


def test_accepted_job_has_a_future_appointment_and_shows_contact_data(seeded, client):
    harbor = TRADESPEOPLE["harbor"]
    with seeded.app_context():
        job = db.session.scalar(db.select(Job).filter_by(title=JOBS["shower"]["title"]))
        job_id = job.id
        booking = job.bookings[0]
        assert booking.starts_at > now_local()
        accepted = [quote for quote in job.trades[0].quotes if quote.status == QUOTE_ACCEPTED]
        assert [quote.tradesperson.email for quote in accepted] == [harbor["email"]]
    log_in(client, harbor["email"])
    assert JOBS["shower"]["title"] in page(client, "/bookings/appointments")
    assert CUSTOMERS["emma"][2] in page(client, f"/jobs/{job_id}")


def test_customer_contact_stays_hidden_on_an_open_job(seeded, client):
    log_in(client, TRADESPEOPLE["harbor"]["email"])
    with seeded.app_context():
        job_id = db.session.scalar(db.select(Job.id).filter_by(title=JOBS["leak"]["title"]))
    detail = page(client, f"/jobs/{job_id}")
    assert CUSTOMERS["emma"][2] not in detail
    assert CUSTOMERS["emma"][3] not in detail


def test_bookings_match_a_real_weekly_slot(seeded):
    with seeded.app_context():
        for booking in db.session.scalars(db.select(Booking)):
            starts_at = booking.starts_at
            slots = booking.tradesperson.profile.availability
            assert any(s.weekday == starts_at.weekday() and s.start_time == starts_at.time() for s in slots)


def test_every_accepted_quote_has_its_appointment(seeded):
    with seeded.app_context():
        accepted = db.session.scalars(db.select(Quote).filter_by(status=QUOTE_ACCEPTED)).all()
        assert len(accepted) == 4
        for quote in accepted:
            bookings = [b for b in quote.job_trade.job.bookings if b.tradesperson_id == quote.tradesperson_id]
            assert len(bookings) == 1
            assert bookings[0].starts_at.date() >= quote.earliest_start


def test_project_timeline_matches_the_appointments(seeded):
    with seeded.app_context():
        job = db.session.scalar(db.select(Job).filter_by(title=JOBS["kitchen"]["title"]))
        starts = {booking.tradesperson_id: booking.starts_at.date() for booking in job.bookings}
        for schedule in job.project.schedules:
            assert schedule.start_date == starts[schedule.tradesperson_id]
        by_trade = {schedule.job_trade.trade: schedule for schedule in job.project.schedules}
        assert by_trade["Tiler"].end_date < by_trade["Carpenter"].start_date


def test_demo_thread_shows_a_new_message_to_the_tradesperson_only(seeded, client):
    log_in(client, TRADESPEOPLE["harbor"]["email"])
    assert "1 new" in page(client, "/messages/")

    client.post("/auth/logout")
    log_in(client, customer_email("emma"))
    assert "1 new" not in page(client, "/messages/")


def test_small_open_job_offers_free_slots(seeded, client):
    log_in(client, customer_email("olivia"))
    assert TRADESPEOPLE["allround"]["company"] in page(client, "/bookings/job/5")


def test_project_page_shows_members_dates_and_comments(seeded, client):
    log_in(client, customer_email("olivia"))
    project = page(client, "/projects/1")
    assert TRADESPEOPLE["tiling"]["name"] in project
    assert TRADESPEOPLE["oak"]["name"] in project
    assert project.count("<strong>Tiler</strong>") == 1
    assert "The tiles need to be finished" in project
    with seeded.app_context():
        proposed = [comment.proposed_date for comment in db.session.scalars(db.select(ProjectComment))]
        assert sum(date is not None for date in proposed) == 2


@pytest.mark.parametrize("key", ["tiling", "oak"])
def test_project_members_can_open_the_project(seeded, client, key):
    log_in(client, TRADESPEOPLE[key]["email"])
    assert client.get("/projects/1").status_code == 200


def test_outsiders_cannot_open_the_project(seeded, client):
    log_in(client, TRADESPEOPLE["harbor"]["email"])
    assert client.get("/projects/1").status_code == 403


def test_done_job_has_a_review_that_shows_up(seeded, client):
    with seeded.app_context():
        review = db.session.scalar(db.select(Review))
        assert review.job.status == JOB_DONE
        person_id = review.tradesperson_id
    log_in(client, customer_email("thomas"))
    assert "Rating: 5.0" in page(client, "/reviews/tradespeople")
    assert "Careful, clean work" in page(client, f"/reviews/tradespeople/{person_id}")


def test_admin_lists_are_not_empty(app, seeded, client):
    with app.app_context():
        admin = User(email="boss@example.com", name="Admin", role=ROLE_ADMIN, email_verified=True)
        admin.set_password(DEMO_PASSWORD)
        db.session.add(admin)
        db.session.commit()
    log_in(client, "boss@example.com")
    admin_page = page(client, "/admin/tradespeople")
    assert TRADESPEOPLE["newstart"]["company"] in admin_page
    assert f"{TRADESPEOPLE['roofing']['company']}: Carpenter" in admin_page


def test_pending_trade_is_stored_as_pending(seeded):
    with seeded.app_context():
        pending = db.session.scalars(
            db.select(TradespersonTrade).filter_by(status=TRADE_PENDING).order_by(TradespersonTrade.trade)
        ).all()
        assert [entry.trade for entry in pending] == ["Carpenter", "Painter"]


# --- Durations in the demo data ----------------------------------------------------------


def test_every_demo_quote_states_a_duration_that_fits_a_slot(seeded):
    with seeded.app_context():
        for quote in db.session.scalars(db.select(Quote)):
            assert quote.duration_minutes % 30 == 0 and 30 <= quote.duration_minutes <= 480
            slots = quote.tradesperson.profile.availability
            longest = max(
                (datetime.combine(date.min, s.end_time) - datetime.combine(date.min, s.start_time)).seconds // 60
                for s in slots
            )
            assert quote.duration_minutes <= longest


def test_demo_appointments_last_as_long_as_the_accepted_quote_and_never_overlap(seeded):
    with seeded.app_context():
        for booking in db.session.scalars(db.select(Booking)):
            accepted = [
                quote.duration_minutes
                for row in booking.job.trades
                for quote in row.quotes
                if quote.tradesperson_id == booking.tradesperson_id and quote.status == QUOTE_ACCEPTED
            ]
            if accepted:  # small jobs booked into a slot have no quote
                assert booking.duration_minutes == accepted[0]
        for booking in db.session.scalars(db.select(Booking)):
            others = [b for b in booking.tradesperson.bookings if b.id != booking.id]
            assert not any(b.starts_at < booking.ends_at and booking.starts_at < b.ends_at for b in others)


def test_demo_users_have_accepted_the_current_terms(seeded):
    from app.models import TERMS_VERSION

    with seeded.app_context():
        users = db.session.scalars(db.select(User).where(User.role != "admin")).all()
        assert users and all(user.terms_version == TERMS_VERSION for user in users)
