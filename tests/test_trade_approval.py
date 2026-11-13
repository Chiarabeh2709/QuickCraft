from datetime import date, time, timedelta

import pytest

from app import create_app
from app.blueprints.bookings import now_local
from app.extensions import db
from app.models import (
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
    AvailabilitySlot,
    Booking,
    Job,
    JobTrade,
    Quote,
    TradespersonProfile,
    TradespersonTrade,
    User,
)
from config import TestConfig
from tests.conftest import PASSWORD

CUSTOMER = "anna@example.com"
BEN = "ben@example.com"
OTHER = "tina@example.com"
ADMIN = "admin@example.com"
COMPANY = "Ben Plumbing"


@pytest.fixture
def make_tradesperson(app, make_user):
    """Creates a tradesperson. `trades` maps a trade to its status. Returns the profile id."""

    def _make(email=BEN, trades=None, status=VERIFICATION_VERIFIED, company=COMPANY):
        user_id = make_user(email=email, role=ROLE_TRADESPERSON, status=status, email_verified=True)
        trades = {"Plumber": TRADE_APPROVED} if trades is None else trades
        with app.app_context():
            profile = TradespersonProfile(
                user_id=user_id,
                company_name=company,
                service_area="Berlin",
                qualification_reference="HWK-1",
                trades=[TradespersonTrade(trade=trade, status=value) for trade, value in trades.items()],
            )
            db.session.add(profile)
            db.session.commit()
            return profile.id

    return _make


@pytest.fixture
def customer_id(make_user):
    return make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=True)


@pytest.fixture
def admin_id(make_user):
    return make_user(email=ADMIN, role=ROLE_ADMIN, email_verified=True)


@pytest.fixture
def make_job(app):
    def _make(customer, trades=("Electrician",), is_small=False):
        with app.app_context():
            job = Job(
                customer_id=customer,
                title="Wiring job",
                city="Berlin",
                description="Please help",
                urgency="normal",
                is_small=is_small,
                trades=[JobTrade(trade=trade) for trade in trades],
            )
            db.session.add(job)
            db.session.commit()
            return job.id

    return _make


def trades_of(app, profile_id):
    with app.app_context():
        return {entry.trade: entry.status for entry in db.session.get(TradespersonProfile, profile_id).trades}


def trade_id(app, profile_id, trade):
    with app.app_context():
        return db.session.scalar(db.select(TradespersonTrade.id).filter_by(profile_id=profile_id, trade=trade))


def edit_form(*trades, **changes):
    return {"description": "Text", "service_area": "Berlin", "trades": list(trades), **changes}


def set_status(app, profile_id, trade, status):
    with app.app_context():
        db.session.scalar(db.select(TradespersonTrade).filter_by(profile_id=profile_id, trade=trade)).status = status
        db.session.commit()


def account_status(app, email):
    with app.app_context():
        return db.session.scalar(db.select(User).filter_by(email=email)).verification_status


def page_of(client, url, **params):
    return client.get(url, query_string=params).get_data(as_text=True)


def switch_user(client, login, email):
    client.post("/auth/logout")
    login(email)


# --- Adding a trade -----------------------------------------------------------------------


def test_adding_a_trade_makes_it_pending_and_account_stays_verified(app, client, make_tradesperson, login):
    profile_id = make_tradesperson()
    login(BEN)
    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber", "Electrician"))
    assert trades_of(app, profile_id) == {"Plumber": TRADE_APPROVED, "Electrician": TRADE_PENDING}
    assert account_status(app, BEN) == VERIFICATION_VERIFIED


def test_adding_a_trade_tells_the_tradesperson_it_waits_for_approval(client, make_tradesperson, login):
    profile_id = make_tradesperson()
    login(BEN)
    response = client.post(
        f"/profiles/{profile_id}/edit", data=edit_form("Plumber", "Electrician"), follow_redirects=True
    )
    assert "Waiting for admin approval: Electrician" in response.get_data(as_text=True)


def test_saving_without_new_trades_changes_no_status(app, client, make_tradesperson, login):
    trades = {"Plumber": TRADE_APPROVED, "Tiler": TRADE_PENDING, "Roofer": TRADE_REJECTED}
    profile_id = make_tradesperson(trades=trades)
    login(BEN)
    response = client.post(
        f"/profiles/{profile_id}/edit", data=edit_form("Plumber", "Tiler", "Roofer"), follow_redirects=True
    )
    assert trades_of(app, profile_id) == trades
    assert "Waiting for admin approval" not in response.get_data(as_text=True)


# --- A crafted POST -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"status": TRADE_APPROVED},
        {"trade_status": TRADE_APPROVED},
        {"trade_status[Electrician]": TRADE_APPROVED},
        {"Electrician": TRADE_APPROVED},
        {"approved_trades": ["Electrician"]},
        {"trades[0][status]": TRADE_APPROVED},
    ],
)
def test_crafted_post_cannot_create_an_approved_trade(app, client, make_tradesperson, login, extra):
    profile_id = make_tradesperson()
    login(BEN)
    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber", "Electrician", **extra))
    assert trades_of(app, profile_id)["Electrician"] == TRADE_PENDING


def test_crafted_post_cannot_approve_a_pending_trade_by_sending_it_again(app, client, make_tradesperson, login):
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Tiler": TRADE_PENDING})
    login(BEN)
    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber", "Tiler", status=TRADE_APPROVED))
    assert trades_of(app, profile_id)["Tiler"] == TRADE_PENDING


def test_crafted_post_cannot_turn_a_rejected_trade_pending_or_approved(app, client, make_tradesperson, login):
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Tiler": TRADE_REJECTED})
    login(BEN)
    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber", "Tiler", status=TRADE_APPROVED))
    assert trades_of(app, profile_id)["Tiler"] == TRADE_REJECTED


def test_removing_and_adding_again_does_not_keep_the_approval(app, client, make_tradesperson, login):
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Tiler": TRADE_APPROVED})
    login(BEN)
    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber"))
    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber", "Tiler"))
    assert trades_of(app, profile_id)["Tiler"] == TRADE_PENDING


def test_new_trade_row_is_pending_by_default(app, make_tradesperson):
    profile_id = make_tradesperson(trades={})
    with app.app_context():
        db.session.add(TradespersonTrade(profile_id=profile_id, trade="Tiler"))
        db.session.commit()
    assert trades_of(app, profile_id) == {"Tiler": TRADE_PENDING}


# --- Removing a trade ---------------------------------------------------------------------


@pytest.mark.parametrize("status", [TRADE_APPROVED, TRADE_PENDING, TRADE_REJECTED])
def test_removing_a_trade_works_for_every_status(app, client, make_tradesperson, login, status):
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Tiler": status})
    login(BEN)
    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber"))
    assert trades_of(app, profile_id) == {"Plumber": TRADE_APPROVED}
    assert account_status(app, BEN) == VERIFICATION_VERIFIED


def test_removed_trade_no_longer_gives_jobs_or_search(client, customer_id, make_tradesperson, make_job, login):
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": TRADE_APPROVED})
    job_id = make_job(customer_id)
    login(BEN)
    assert client.get(f"/jobs/{job_id}").status_code == 200

    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber"))

    assert client.get(f"/jobs/{job_id}").status_code == 404
    switch_user(client, login, CUSTOMER)
    assert COMPANY not in page_of(client, "/profiles/", trade="Electrician")


def test_removing_a_trade_keeps_an_accepted_quote_working(app, client, customer_id, make_tradesperson, make_job, login):
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": TRADE_APPROVED})
    job_id = make_job(customer_id)
    with app.app_context():
        job_trade = db.session.scalar(db.select(JobTrade).filter_by(job_id=job_id))
        user = db.session.scalar(db.select(User).filter_by(email=BEN))
        db.session.add(
            Quote(
                job_trade_id=job_trade.id,
                tradesperson_id=user.id,
                price_cents=100,
                earliest_start=date(2030, 1, 1),
                status=QUOTE_ACCEPTED,
            )
        )
        db.session.commit()
    login(BEN)

    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber"))

    assert client.get(f"/jobs/{job_id}").status_code == 200


# --- Public profile and search ------------------------------------------------------------


@pytest.mark.parametrize("status", [TRADE_PENDING, TRADE_REJECTED])
def test_unapproved_trade_is_not_on_the_public_profile(client, customer_id, make_tradesperson, login, status):
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": status})
    url = f"/profiles/{profile_id}"
    assert "Plumber" in page_of(client, url)
    assert "Electrician" not in page_of(client, url)
    login(CUSTOMER)
    assert "Electrician" not in page_of(client, url)


@pytest.mark.parametrize("status", [TRADE_PENDING, TRADE_REJECTED])
def test_unapproved_trade_is_hidden_from_other_tradespeople(client, make_tradesperson, login, status):
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": status})
    make_tradesperson(email=OTHER, company="Tina Tiles")
    login(OTHER)
    page = page_of(client, f"/profiles/{profile_id}")
    assert "Electrician" not in page
    assert "waiting for approval" not in page


@pytest.mark.parametrize("status", [TRADE_PENDING, TRADE_REJECTED])
def test_unapproved_trade_does_not_find_the_profile_in_search(client, customer_id, make_tradesperson, login, status):
    make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": status})
    login(CUSTOMER)
    assert COMPANY not in page_of(client, "/profiles/", trade="Electrician")
    assert COMPANY in page_of(client, "/profiles/", trade="Plumber")


@pytest.mark.parametrize("status", [TRADE_PENDING, TRADE_REJECTED])
def test_unapproved_trade_is_not_shown_in_search_results(client, customer_id, make_tradesperson, login, status):
    make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Roofer": status})
    login(CUSTOMER)
    page = page_of(client, "/profiles/", trade="Plumber")
    # The filter dropdown lists every trade, so look at the result card only.
    assert "Roofer &middot;" not in page
    assert "Plumber &middot;" in page


def test_tradesperson_without_approved_trade_is_not_in_search(client, customer_id, make_tradesperson, login):
    make_tradesperson(trades={"Electrician": TRADE_PENDING})
    login(CUSTOMER)
    assert COMPANY not in page_of(client, "/profiles/")


def test_profile_without_approved_trade_still_loads(client, make_tradesperson):
    response = client.get(f"/profiles/{make_tradesperson(trades={'Electrician': TRADE_PENDING})}")
    assert response.status_code == 200
    assert "Electrician" not in response.get_data(as_text=True)


# --- Jobs ---------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [TRADE_PENDING, TRADE_REJECTED])
def test_unapproved_trade_gives_no_access_to_jobs(client, customer_id, make_tradesperson, make_job, login, status):
    make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": status})
    job_id = make_job(customer_id)
    login(BEN)
    assert "Wiring job" not in page_of(client, "/jobs/")
    assert "Wiring job" not in page_of(client, "/jobs/", trade="Electrician")
    assert client.get(f"/jobs/{job_id}").status_code == 404


def test_job_filter_only_offers_approved_trades(client, make_tradesperson, login):
    make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": TRADE_PENDING})
    login(BEN)
    page = page_of(client, "/jobs/")
    assert '<option value="Plumber"' in page
    assert '<option value="Electrician"' not in page


def test_existing_approved_trade_keeps_its_jobs_while_another_is_pending(
    client, customer_id, make_tradesperson, make_job, login
):
    profile_id = make_tradesperson()
    job_id = make_job(customer_id, trades=("Plumber",))
    login(BEN)
    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber", "Electrician"))
    assert client.get(f"/jobs/{job_id}").status_code == 200
    assert "Wiring job" in page_of(client, "/jobs/")


# --- Quotes and bookings ------------------------------------------------------------------


def quote_form(job_trade):
    return {"job_trade_id": job_trade, "price": "150.00", "earliest_start": "2031-01-06", "duration": "120", "message": "Hello"}


def job_trade_id(app, job_id, trade):
    with app.app_context():
        return db.session.scalar(db.select(JobTrade.id).filter_by(job_id=job_id, trade=trade))


def quote_count(app):
    with app.app_context():
        return db.session.query(Quote).count()


def test_pending_trade_cannot_be_quoted_directly(app, client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": TRADE_PENDING})
    job_id = make_job(customer_id)
    login(BEN)
    assert client.post(f"/quotes/job/{job_id}/new", data=quote_form(job_trade_id(app, job_id, "Electrician"))).status_code == 404
    assert quote_count(app) == 0


@pytest.mark.parametrize("status", [TRADE_PENDING, TRADE_REJECTED])
def test_multi_trade_job_does_not_allow_a_quote_for_the_unapproved_trade(
    app, client, customer_id, make_tradesperson, make_job, login, status
):
    # The approved trade makes the job visible, so the quote form itself must refuse the other trade.
    make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": status})
    job_id = make_job(customer_id, trades=("Plumber", "Electrician"))
    login(BEN)

    assert "Electrician" not in page_of(client, f"/quotes/job/{job_id}/new").split("<form", 1)[1]

    client.post(f"/quotes/job/{job_id}/new", data=quote_form(job_trade_id(app, job_id, "Electrician")))
    assert quote_count(app) == 0

    client.post(f"/quotes/job/{job_id}/new", data=quote_form(job_trade_id(app, job_id, "Plumber")))
    assert quote_count(app) == 1


@pytest.fixture
def slot_tomorrow(app):
    def _make(profile_id):
        tomorrow = now_local().date() + timedelta(days=1)
        with app.app_context():
            slot = AvailabilitySlot(
                profile_id=profile_id, weekday=tomorrow.weekday(), start_time=time(9), end_time=time(12)
            )
            db.session.add(slot)
            db.session.commit()
            return slot.id, tomorrow

    return _make


def test_pending_trade_is_not_offered_for_booking(app, client, customer_id, make_tradesperson, make_job, slot_tomorrow, login):
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": TRADE_PENDING})
    slot_id, day = slot_tomorrow(profile_id)
    job_id = make_job(customer_id, is_small=True)
    login(CUSTOMER)

    assert COMPANY not in page_of(client, f"/bookings/job/{job_id}")

    client.post(f"/bookings/job/{job_id}/book", data={"slot_id": slot_id, "date": day.isoformat()})
    with app.app_context():
        assert db.session.query(Booking).count() == 0


def test_approved_trade_is_offered_for_booking(client, customer_id, make_tradesperson, make_job, slot_tomorrow, login):
    profile_id = make_tradesperson(trades={"Electrician": TRADE_APPROVED})
    slot_tomorrow(profile_id)
    job_id = make_job(customer_id, is_small=True)
    login(CUSTOMER)
    assert COMPANY in page_of(client, f"/bookings/job/{job_id}")


# --- Approving a trade makes all of it work -----------------------------------------------


def test_approving_a_trade_opens_profile_search_jobs_and_quotes(
    app, client, customer_id, admin_id, make_tradesperson, make_job, login
):
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": TRADE_PENDING})
    job_id = make_job(customer_id, trades=("Plumber", "Electrician"))
    only_electrician_job = make_job(customer_id, trades=("Electrician",))
    login(ADMIN)

    client.post(f"/admin/trades/{trade_id(app, profile_id, 'Electrician')}/approve")

    assert trades_of(app, profile_id)["Electrician"] == TRADE_APPROVED
    assert "Electrician" in page_of(client, f"/profiles/{profile_id}")
    switch_user(client, login, CUSTOMER)
    assert COMPANY in page_of(client, "/profiles/", trade="Electrician")
    switch_user(client, login, BEN)
    assert client.get(f"/jobs/{only_electrician_job}").status_code == 200
    client.post(f"/quotes/job/{job_id}/new", data=quote_form(job_trade_id(app, job_id, "Electrician")))
    assert quote_count(app) == 1


def test_rejecting_a_trade_keeps_it_out_and_the_account_verified(
    app, client, customer_id, admin_id, make_tradesperson, make_job, login
):
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": TRADE_PENDING})
    job_id = make_job(customer_id)
    login(ADMIN)

    client.post(f"/admin/trades/{trade_id(app, profile_id, 'Electrician')}/reject")

    assert trades_of(app, profile_id) == {"Plumber": TRADE_APPROVED, "Electrician": TRADE_REJECTED}
    assert account_status(app, BEN) == VERIFICATION_VERIFIED
    switch_user(client, login, BEN)
    assert client.get(f"/jobs/{job_id}").status_code == 404


# --- Own labels ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["/profiles/{}", "/profiles/{}/edit"])
def test_owner_sees_labels_on_profile_and_edit_page(client, make_tradesperson, login, url):
    profile_id = make_tradesperson(
        trades={"Plumber": TRADE_APPROVED, "Electrician": TRADE_PENDING, "Roofer": TRADE_REJECTED}
    )
    login(BEN)
    page = page_of(client, url.format(profile_id))
    assert "Electrician" in page
    assert "waiting for approval" in page
    assert "Roofer" in page
    assert ">rejected<" in page


def test_no_labels_when_all_trades_are_approved(client, make_tradesperson, login):
    profile_id = make_tradesperson()
    login(BEN)
    for url in (f"/profiles/{profile_id}", f"/profiles/{profile_id}/edit"):
        page = page_of(client, url)
        assert "waiting for approval" not in page
        assert ">rejected<" not in page


# --- Sign-up trades go with the account ---------------------------------------------------


SIGNUP_FORM = {
    "name": "Ben",
    "email": BEN,
    "password": "correct-horse", "accept_terms": "1",
    "phone": "+49 170 5550123",
    "street": "Hauptstrasse 12",
    "postal_code": "10115",
    "city": "Berlin",
    "company_name": COMPANY,
    "trade": "Plumber",
    "service_area": "Berlin",
    "qualification_reference": "HWK-1",
}


def signup_profile_id(app, client):
    client.post("/auth/signup/tradesperson", data=SIGNUP_FORM)
    client.post("/auth/logout")
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(email=BEN))
        return user.id, user.profile.id


def test_signup_trade_starts_pending(app, client):
    _, profile_id = signup_profile_id(app, client)
    assert trades_of(app, profile_id) == {"Plumber": TRADE_PENDING}


def test_approving_the_account_approves_its_signup_trades(app, client, admin_id, login):
    user_id, profile_id = signup_profile_id(app, client)
    login(ADMIN)
    client.post(f"/admin/tradespeople/{user_id}/approve")
    assert trades_of(app, profile_id) == {"Plumber": TRADE_APPROVED}
    assert account_status(app, BEN) == VERIFICATION_VERIFIED


def test_rejecting_the_account_rejects_its_signup_trades(app, client, admin_id, login):
    user_id, profile_id = signup_profile_id(app, client)
    login(ADMIN)
    client.post(f"/admin/tradespeople/{user_id}/reject")
    assert trades_of(app, profile_id) == {"Plumber": TRADE_REJECTED}


def test_full_path_from_signup_to_jobs(app, client, customer_id, admin_id, make_job, login):
    job_id = make_job(customer_id, trades=("Plumber",))
    user_id, _ = signup_profile_id(app, client)
    login(ADMIN)
    client.post(f"/admin/tradespeople/{user_id}/approve")
    switch_user(client, login, BEN)
    assert client.get(f"/jobs/{job_id}").status_code == 200


# --- Admin page ---------------------------------------------------------------------------


def test_admin_page_lists_pending_trades(client, admin_id, make_tradesperson, login):
    make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": TRADE_PENDING})
    login(ADMIN)
    page = page_of(client, "/admin/tradespeople")
    assert "Pending trades" in page
    assert f"{COMPANY}: Electrician" in page
    assert f"{COMPANY}: Plumber" not in page


def test_admin_page_hides_private_data(app, client, admin_id, make_tradesperson, login):
    make_tradesperson(trades={"Electrician": TRADE_PENDING})
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(email=BEN))
        user.phone, user.address = "+49 40 5550777", "Werkstrasse 1, 20095 Hamburg"
        db.session.commit()
    login(ADMIN)
    page = page_of(client, "/admin/tradespeople")
    assert "+49 40 5550777" not in page and "Werkstrasse" not in page


@pytest.mark.parametrize("status", [VERIFICATION_PENDING, VERIFICATION_REJECTED])
def test_trades_of_unverified_accounts_are_not_in_the_list(client, admin_id, make_tradesperson, login, status):
    make_tradesperson(trades={"Electrician": TRADE_PENDING}, status=status)
    login(ADMIN)
    assert f"{COMPANY}: Electrician" not in page_of(client, "/admin/tradespeople")


@pytest.mark.parametrize("status", [VERIFICATION_PENDING, VERIFICATION_REJECTED])
def test_trade_of_unverified_account_cannot_be_decided_alone(app, client, admin_id, make_tradesperson, login, status):
    profile_id = make_tradesperson(trades={"Electrician": TRADE_PENDING}, status=status)
    login(ADMIN)
    client.post(f"/admin/trades/{trade_id(app, profile_id, 'Electrician')}/approve")
    assert trades_of(app, profile_id) == {"Electrician": TRADE_PENDING}


# --- Only an admin can decide -------------------------------------------------------------


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_anonymous_visitor_cannot_decide_a_trade(app, client, make_tradesperson, decision):
    profile_id = make_tradesperson(trades={"Electrician": TRADE_PENDING})
    response = client.post(f"/admin/trades/{trade_id(app, profile_id, 'Electrician')}/{decision}")
    assert response.status_code == 302
    assert "/auth/login" in response.location
    assert trades_of(app, profile_id) == {"Electrician": TRADE_PENDING}


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_tradesperson_cannot_decide_own_trade(app, client, make_tradesperson, login, decision):
    profile_id = make_tradesperson(trades={"Electrician": TRADE_PENDING})
    login(BEN)
    response = client.post(f"/admin/trades/{trade_id(app, profile_id, 'Electrician')}/{decision}")
    assert response.status_code == 403
    assert trades_of(app, profile_id) == {"Electrician": TRADE_PENDING}


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_customer_cannot_decide_a_trade(app, client, customer_id, make_tradesperson, login, decision):
    profile_id = make_tradesperson(trades={"Electrician": TRADE_PENDING})
    login(CUSTOMER)
    response = client.post(f"/admin/trades/{trade_id(app, profile_id, 'Electrician')}/{decision}")
    assert response.status_code == 403
    assert trades_of(app, profile_id) == {"Electrician": TRADE_PENDING}


def test_deciding_a_trade_needs_post(app, client, admin_id, make_tradesperson, login):
    profile_id = make_tradesperson(trades={"Electrician": TRADE_PENDING})
    login(ADMIN)
    assert client.get(f"/admin/trades/{trade_id(app, profile_id, 'Electrician')}/approve").status_code == 405


def test_deciding_a_trade_checks_csrf_token():
    class CsrfOn(TestConfig):
        WTF_CSRF_ENABLED = True

    app = create_app(CsrfOn)
    with app.app_context():
        admin = User(email=ADMIN, name="Admin", role=ROLE_ADMIN, email_verified=True)
        admin.set_password(PASSWORD)
        person = User(
            email=BEN, name="Ben", role=ROLE_TRADESPERSON, verification_status=VERIFICATION_VERIFIED, email_verified=True
        )
        person.set_password(PASSWORD)
        person.accept_terms()
        person.profile = TradespersonProfile(
            company_name=COMPANY, service_area="Berlin", trades=[TradespersonTrade(trade="Electrician")]
        )
        db.session.add_all([admin, person])
        db.session.commit()
        admin_user_id, entry_id, profile_id = admin.id, person.profile.trades[0].id, person.profile.id

    client = app.test_client()
    # Logging in also needs a token here, so put the login straight into the session.
    with client.session_transaction() as session:
        session["_user_id"] = str(admin_user_id)
        session["_fresh"] = True

    assert client.post(f"/admin/trades/{entry_id}/approve").status_code == 400
    assert trades_of(app, profile_id) == {"Electrician": TRADE_PENDING}


# --- Stale tabs ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "first, second, expected",
    [
        ("approve", "reject", TRADE_APPROVED),
        ("reject", "approve", TRADE_REJECTED),
    ],
)
def test_decision_cannot_be_changed_from_a_stale_tab(app, client, admin_id, make_tradesperson, login, first, second, expected):
    profile_id = make_tradesperson(trades={"Electrician": TRADE_PENDING})
    entry_id = trade_id(app, profile_id, "Electrician")
    login(ADMIN)
    client.post(f"/admin/trades/{entry_id}/{first}")
    response = client.post(f"/admin/trades/{entry_id}/{second}", follow_redirects=True)
    assert "no longer pending" in response.get_data(as_text=True)
    assert trades_of(app, profile_id) == {"Electrician": expected}


def test_stale_tab_cannot_approve_a_removed_trade(app, client, admin_id, make_tradesperson, login):
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": TRADE_PENDING})
    entry_id = trade_id(app, profile_id, "Electrician")
    login(BEN)
    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber"))
    switch_user(client, login, ADMIN)
    response = client.post(f"/admin/trades/{entry_id}/approve", follow_redirects=True)
    assert "no longer pending" in response.get_data(as_text=True)
    assert trades_of(app, profile_id) == {"Plumber": TRADE_APPROVED}


def test_stale_tab_cannot_approve_another_trade_that_got_the_same_id(app, client, admin_id, make_tradesperson, login):
    # SQLite reuses the highest deleted id unless told otherwise, so this approve could hit the wrong trade.
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Electrician": TRADE_PENDING})
    stale_id = trade_id(app, profile_id, "Electrician")
    login(BEN)
    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber"))
    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber", "Roofer"))
    assert trade_id(app, profile_id, "Roofer") != stale_id
    switch_user(client, login, ADMIN)

    response = client.post(f"/admin/trades/{stale_id}/approve", follow_redirects=True)

    assert "no longer pending" in response.get_data(as_text=True)
    assert trades_of(app, profile_id) == {"Plumber": TRADE_APPROVED, "Roofer": TRADE_PENDING}


def test_rejected_trade_can_be_requested_again_but_only_as_pending(app, client, make_tradesperson, login):
    profile_id = make_tradesperson(trades={"Plumber": TRADE_APPROVED, "Tiler": TRADE_REJECTED})
    login(BEN)
    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber"))
    client.post(f"/profiles/{profile_id}/edit", data=edit_form("Plumber", "Tiler"))
    assert trades_of(app, profile_id)["Tiler"] == TRADE_PENDING


def test_unknown_trade_is_handled(client, admin_id, login):
    login(ADMIN)
    response = client.post("/admin/trades/999/approve", follow_redirects=True)
    assert "no longer pending" in response.get_data(as_text=True)
