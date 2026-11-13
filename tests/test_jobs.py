from datetime import date, datetime

import pytest

from app.extensions import db
from app.models import (
    JOB_CANCELLED,
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


# --- Helpers: the quotes and bookings features are not merged, so tests create their own data ---


@pytest.fixture
def customer_id(app, make_user):
    user_id = make_user(email="anna@example.com", email_verified=True)
    with app.app_context():
        user = db.session.get(User, user_id)
        user.phone = PHONE
        user.address = ADDRESS
        db.session.commit()
    return user_id


@pytest.fixture
def make_tradesperson(app, make_user):
    def _make(email="ben@example.com", trades=("Plumber",), area="Berlin", status=VERIFICATION_VERIFIED):
        user_id = make_user(email=email, role=ROLE_TRADESPERSON, status=status, email_verified=True)
        with app.app_context():
            db.session.add(
                TradespersonProfile(
                    user_id=user_id,
                    company_name="Test GmbH",
                    service_area=area,
                    trades=[TradespersonTrade(trade=trade, status=TRADE_APPROVED) for trade in trades],
                )
            )
            db.session.commit()
        return user_id

    return _make


@pytest.fixture
def make_job(app):
    def _make(customer, title="Leaky tap", city="Berlin", trades=("Plumber",), status=JOB_OPEN, urgency="normal"):
        with app.app_context():
            job = Job(
                customer_id=customer,
                title=title,
                city=city,
                description="Please fix it.",
                urgency=urgency,
                status=status,
                trades=[JobTrade(trade=trade) for trade in trades],
            )
            db.session.add(job)
            db.session.commit()
            return job.id

    return _make


@pytest.fixture
def add_quote(app):
    def _add(job_id, tradesperson, trade="Plumber", status=QUOTE_ACCEPTED):
        with app.app_context():
            job_trade = db.session.scalar(db.select(JobTrade).filter_by(job_id=job_id, trade=trade))
            db.session.add(
                Quote(
                    job_trade_id=job_trade.id,
                    tradesperson_id=tradesperson,
                    price_cents=15000,
                    earliest_start=date(2030, 1, 1),
                    status=status,
                )
            )
            db.session.commit()

    return _add


@pytest.fixture
def add_booking(app):
    def _add(job_id, tradesperson):
        with app.app_context():
            db.session.add(Booking(job_id=job_id, tradesperson_id=tradesperson, starts_at=datetime(2030, 1, 1, 9)))
            db.session.commit()

    return _add


def job_status(app, job_id):
    with app.app_context():
        return db.session.get(Job, job_id).status


def job_count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Job.id)))


def assert_sent_to_login(response):
    assert response.status_code == 302
    assert "/auth/login" in response.location


VALID_FORM = {
    "title": "Leaky tap",
    "description": "The kitchen tap drips all night.",
    "city": "Berlin",
    "trades": ["Plumber"],
}


# --- 1) Posting a job -------------------------------------------------------------------


def test_confirmed_customer_can_post_job_with_several_trades(app, client, customer_id, login):
    login("anna@example.com")
    form = {**VALID_FORM, "trades": ["Plumber", "Tiler"], "urgent": "1"}
    response = client.post("/jobs/new", data=form)

    assert response.status_code == 302
    with app.app_context():
        job = db.session.scalar(db.select(Job))
        assert (job.title, job.city, job.urgency, job.customer_id) == ("Leaky tap", "Berlin", "urgent", customer_id)
        assert sorted(entry.trade for entry in job.trades) == ["Plumber", "Tiler"]
        assert job.status == JOB_OPEN


def test_job_is_not_urgent_unless_ticked(app, client, customer_id, login):
    login("anna@example.com")
    client.post("/jobs/new", data=VALID_FORM)
    with app.app_context():
        assert db.session.scalar(db.select(Job)).urgency == "normal"


@pytest.mark.parametrize(
    "changes",
    [
        {"title": " "},
        {"description": ""},
        {"city": ""},
        {"trades": []},
        {"trades": ["Wizard"]},  # not in the fixed trade list
        {"title": "x" * 121},
        {"description": "x" * 2001},
    ],
)
def test_invalid_job_is_not_saved(app, client, customer_id, login, changes):
    login("anna@example.com")
    response = client.post("/jobs/new", data={**VALID_FORM, **changes})
    assert response.status_code == 200
    assert job_count(app) == 0


def test_posting_form_loads_for_confirmed_customer(client, customer_id, login):
    login("anna@example.com")
    response = client.get("/jobs/new")
    assert response.status_code == 200
    assert b"Electrician" in response.data


def test_anonymous_visitor_cannot_post_job(app, client):
    assert_sent_to_login(client.get("/jobs/new"))
    assert_sent_to_login(client.post("/jobs/new", data=VALID_FORM))
    assert job_count(app) == 0


@pytest.mark.parametrize("method", ["get", "post"])
def test_unconfirmed_customer_cannot_post_job(app, client, make_user, login, method):
    make_user(email="new@example.com", email_verified=False)
    login("new@example.com")
    response = getattr(client, method)("/jobs/new", data=VALID_FORM)
    assert response.status_code == 302
    assert "/auth/confirm-email" in response.location
    assert job_count(app) == 0


@pytest.mark.parametrize(
    "role, status",
    [
        (ROLE_ADMIN, None),
        (ROLE_TRADESPERSON, VERIFICATION_VERIFIED),
        (ROLE_TRADESPERSON, VERIFICATION_PENDING),
    ],
)
def test_only_customers_can_post_jobs(app, client, make_user, login, role, status):
    make_user(email="other@example.com", role=role, status=status, email_verified=True)
    login("other@example.com")
    assert client.get("/jobs/new").status_code == 403
    assert client.post("/jobs/new", data=VALID_FORM).status_code == 403
    assert job_count(app) == 0


# --- 2) Job list and detail for verified tradespeople -----------------------------------


def test_verified_tradesperson_sees_matching_open_jobs_only(client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson()
    make_job(customer_id, title="Plumbing in Berlin")
    make_job(customer_id, title="Electrics in Berlin", trades=("Electrician",))
    make_job(customer_id, title="Plumbing in Hamburg", city="Hamburg")
    make_job(customer_id, title="Cancelled plumbing", status=JOB_CANCELLED)
    make_job(customer_id, title="Plumbing already taken", status=JOB_IN_PROGRESS)
    login("ben@example.com")

    page = client.get("/jobs/").get_data(as_text=True)

    assert "Plumbing in Berlin" in page
    for hidden in ("Electrics", "Hamburg", "Cancelled", "already taken"):
        assert hidden not in page


def test_multi_trade_job_is_listed_for_each_of_its_trades(client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson(trades=("Tiler",))
    make_job(customer_id, title="Bathroom renovation", trades=("Plumber", "Tiler"))
    login("ben@example.com")
    assert "Bathroom renovation" in client.get("/jobs/").get_data(as_text=True)


def test_service_area_can_list_several_cities_and_ignores_case(client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson(area="Hamburg, berlin")
    make_job(customer_id, title="In Berlin", city="Berlin")
    make_job(customer_id, title="In Hamburg", city="Hamburg")
    make_job(customer_id, title="In Munich", city="Munich")
    login("ben@example.com")

    page = client.get("/jobs/").get_data(as_text=True)

    assert "In Berlin" in page and "In Hamburg" in page
    assert "In Munich" not in page


def test_list_can_be_filtered_by_trade(client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson(trades=("Plumber", "Tiler"))
    make_job(customer_id, title="A plumbing job")
    make_job(customer_id, title="A tiling job", trades=("Tiler",))
    login("ben@example.com")

    page = client.get("/jobs/?trade=Tiler").get_data(as_text=True)

    assert "A tiling job" in page
    assert "A plumbing job" not in page


def test_list_can_be_filtered_by_city(client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson(area="Berlin, Hamburg")
    make_job(customer_id, title="In Berlin", city="Berlin")
    make_job(customer_id, title="In Hamburg", city="Hamburg")
    login("ben@example.com")

    page = client.get("/jobs/?city=Hamburg").get_data(as_text=True)

    assert "In Hamburg" in page
    assert "In Berlin" not in page


def test_filter_cannot_widen_the_list_beyond_own_trade_and_area(client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson()
    make_job(customer_id, title="Electrics in Berlin", trades=("Electrician",))
    make_job(customer_id, title="Plumbing in Hamburg", city="Hamburg")
    login("ben@example.com")

    assert "Electrics" not in client.get("/jobs/?trade=Electrician").get_data(as_text=True)
    assert "Hamburg" not in client.get("/jobs/?city=Hamburg").get_data(as_text=True)


def test_list_requires_login(client):
    assert_sent_to_login(client.get("/jobs/"))


@pytest.mark.parametrize(
    "role, status",
    [
        (ROLE_CUSTOMER, None),
        (ROLE_ADMIN, None),
        (ROLE_TRADESPERSON, VERIFICATION_PENDING),
        (ROLE_TRADESPERSON, VERIFICATION_REJECTED),
    ],
)
def test_only_verified_tradespeople_can_see_list_and_detail(client, customer_id, make_job, make_user, login, role, status):
    job_id = make_job(customer_id)
    make_user(email="other@example.com", role=role, status=status, email_verified=True)
    login("other@example.com")
    assert client.get("/jobs/").status_code == 403
    assert client.get(f"/jobs/{job_id}").status_code == 403


def test_detail_shows_matching_job(client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson()
    job_id = make_job(customer_id, title="Leaky tap", urgency="urgent")
    login("ben@example.com")

    response = client.get(f"/jobs/{job_id}")

    assert response.status_code == 200
    assert b"Leaky tap" in response.data
    assert b"Urgent" in response.data


def test_detail_requires_login(client, customer_id, make_job):
    assert_sent_to_login(client.get(f"/jobs/{make_job(customer_id)}"))


@pytest.mark.parametrize(
    "job_fields",
    [
        {"trades": ("Electrician",)},
        {"city": "Hamburg"},
        {"status": JOB_CANCELLED},
        {"status": JOB_IN_PROGRESS},
    ],
)
def test_detail_of_a_job_outside_my_list_is_not_found(client, customer_id, make_tradesperson, make_job, login, job_fields):
    make_tradesperson()
    job_id = make_job(customer_id, **job_fields)
    login("ben@example.com")
    assert client.get(f"/jobs/{job_id}").status_code == 404


def test_detail_of_unknown_job_is_not_found(client, make_tradesperson, login):
    make_tradesperson()
    login("ben@example.com")
    assert client.get("/jobs/9999").status_code == 404


# --- 3) Customers manage their own jobs -------------------------------------------------


def test_customer_sees_own_jobs_of_every_status(client, customer_id, make_job, make_user, login):
    other = make_user(email="other@example.com", email_verified=True)
    make_job(customer_id, title="My open job")
    make_job(customer_id, title="My cancelled job", status=JOB_CANCELLED)
    make_job(other, title="Somebody else's job")
    login("anna@example.com")

    page = client.get("/jobs/mine").get_data(as_text=True)

    assert "My open job" in page and "My cancelled job" in page
    assert "Somebody else" not in page


def test_my_jobs_requires_a_confirmed_customer(client, make_user, make_tradesperson, login):
    assert_sent_to_login(client.get("/jobs/mine"))

    make_user(email="new@example.com", email_verified=False)
    login("new@example.com")
    assert "/auth/confirm-email" in client.get("/jobs/mine").location


def test_tradesperson_cannot_open_my_jobs(client, make_tradesperson, login):
    make_tradesperson()
    login("ben@example.com")
    assert client.get("/jobs/mine").status_code == 403


def test_customer_can_cancel_own_open_job(app, client, customer_id, make_job, login):
    job_id = make_job(customer_id)
    login("anna@example.com")

    response = client.post(f"/jobs/{job_id}/cancel")

    assert response.status_code == 302
    assert job_status(app, job_id) == JOB_CANCELLED


def test_cancelled_job_disappears_from_tradesperson_list(client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson()
    job_id = make_job(customer_id, title="Leaky tap")
    login("anna@example.com")
    client.post(f"/jobs/{job_id}/cancel")
    client.post("/auth/logout")

    login("ben@example.com")
    assert "Leaky tap" not in client.get("/jobs/").get_data(as_text=True)


def test_customer_cannot_cancel_somebody_elses_job(app, client, customer_id, make_job, make_user, login):
    make_user(email="mallory@example.com", email_verified=True)
    job_id = make_job(customer_id)
    login("mallory@example.com")

    assert client.post(f"/jobs/{job_id}/cancel").status_code == 403
    assert job_status(app, job_id) == JOB_OPEN


@pytest.mark.parametrize("status", [JOB_IN_PROGRESS, JOB_CANCELLED])
def test_only_open_jobs_can_be_cancelled(app, client, customer_id, make_job, login, status):
    job_id = make_job(customer_id, status=status)
    login("anna@example.com")

    client.post(f"/jobs/{job_id}/cancel")

    assert job_status(app, job_id) == status


def test_cancel_needs_post(app, client, customer_id, make_job, login):
    job_id = make_job(customer_id)
    login("anna@example.com")
    assert client.get(f"/jobs/{job_id}/cancel").status_code == 405
    assert job_status(app, job_id) == JOB_OPEN


def test_cancel_unknown_job_is_not_found(client, customer_id, login):
    login("anna@example.com")
    assert client.post("/jobs/9999/cancel").status_code == 404


def test_anonymous_visitor_cannot_cancel(app, client, customer_id, make_job):
    job_id = make_job(customer_id)
    assert_sent_to_login(client.post(f"/jobs/{job_id}/cancel"))
    assert job_status(app, job_id) == JOB_OPEN


@pytest.mark.parametrize(
    "role, status",
    [(ROLE_TRADESPERSON, VERIFICATION_VERIFIED), (ROLE_ADMIN, None)],
)
def test_other_roles_cannot_cancel(app, client, customer_id, make_job, make_user, login, role, status):
    make_user(email="other@example.com", role=role, status=status, email_verified=True)
    job_id = make_job(customer_id)
    login("other@example.com")

    assert client.post(f"/jobs/{job_id}/cancel").status_code == 403
    assert job_status(app, job_id) == JOB_OPEN


def test_cancel_form_checks_csrf_token(app, customer_id, make_job, login):
    # The shared test config switches CSRF off, so this test switches it back on.
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    job_id = make_job(customer_id)
    # Logging in also needs a token, so log in directly through the session instead.
    with client.session_transaction() as session:
        session["_user_id"] = str(customer_id)
        session["_fresh"] = True

    assert client.post(f"/jobs/{job_id}/cancel").status_code == 400
    assert job_status(app, job_id) == JOB_OPEN


# --- 4) Contact data privacy ------------------------------------------------------------


def contact_visible(page):
    return PHONE in page or ADDRESS in page


def test_contact_data_is_hidden_on_list_and_detail_before_acceptance(client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson()
    job_id = make_job(customer_id)
    login("ben@example.com")

    assert not contact_visible(client.get("/jobs/").get_data(as_text=True))
    assert not contact_visible(client.get(f"/jobs/{job_id}").get_data(as_text=True))


@pytest.mark.parametrize("status", [QUOTE_PENDING, QUOTE_REJECTED])
def test_contact_data_stays_hidden_for_unaccepted_quote(
    client, customer_id, make_tradesperson, make_job, add_quote, login, status
):
    tradesperson = make_tradesperson()
    job_id = make_job(customer_id)
    add_quote(job_id, tradesperson, status=status)
    login("ben@example.com")

    assert not contact_visible(client.get(f"/jobs/{job_id}").get_data(as_text=True))


def test_contact_data_is_shown_after_quote_is_accepted(client, customer_id, make_tradesperson, make_job, add_quote, login):
    tradesperson = make_tradesperson()
    # Accepting a quote moves the job out of "open"; the accepted tradesperson must still reach it.
    job_id = make_job(customer_id, status=JOB_IN_PROGRESS)
    add_quote(job_id, tradesperson)
    login("ben@example.com")

    detail = client.get(f"/jobs/{job_id}")

    assert detail.status_code == 200
    assert PHONE in detail.get_data(as_text=True) and ADDRESS in detail.get_data(as_text=True)
    # The list only holds open jobs and never shows contact data.
    assert not contact_visible(client.get("/jobs/").get_data(as_text=True))


def test_contact_data_is_shown_after_slot_is_booked(client, customer_id, make_tradesperson, make_job, add_booking, login):
    tradesperson = make_tradesperson()
    job_id = make_job(customer_id, status=JOB_IN_PROGRESS)
    add_booking(job_id, tradesperson)
    login("ben@example.com")

    page = client.get(f"/jobs/{job_id}").get_data(as_text=True)

    assert PHONE in page and ADDRESS in page


def test_accepting_one_tradesperson_does_not_reveal_contact_to_another(
    client, customer_id, make_tradesperson, make_job, add_quote, login
):
    # A multi-trade job stays open after one quote is accepted, so a second tradesperson can still see it.
    accepted = make_tradesperson(email="ben@example.com", trades=("Plumber",))
    make_tradesperson(email="tina@example.com", trades=("Tiler",))
    job_id = make_job(customer_id, trades=("Plumber", "Tiler"))
    add_quote(job_id, accepted, trade="Plumber")
    login("tina@example.com")

    response = client.get(f"/jobs/{job_id}")

    assert response.status_code == 200
    assert not contact_visible(response.get_data(as_text=True))


# --- Contact data entered at sign-up ------------------------------------------------------

SIGNUP_FIELDS = {"phone": "+49 30 5550199", "street": "Gartenweg 7", "postal_code": "10115", "city": "Berlin"}
SIGNUP_ADDRESS = "Gartenweg 7, 10115 Berlin"


@pytest.fixture
def signed_up_customer(app, client):
    """A customer created through the real sign-up form, then logged out."""
    client.post(
        "/auth/signup/customer",
        data={"name": "Clara", "email": "clara@example.com", "password": "correct-horse", **SIGNUP_FIELDS},
    )
    client.post("/auth/logout")
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(email="clara@example.com"))
        user.email_verified = True
        db.session.commit()
        return user.id


def test_signup_contact_data_is_hidden_until_quote_is_accepted(
    client, signed_up_customer, make_tradesperson, make_job, add_quote, login
):
    tradesperson = make_tradesperson()
    job_id = make_job(signed_up_customer)
    login("ben@example.com")

    for url in ("/jobs/", f"/jobs/{job_id}"):
        page = client.get(url).get_data(as_text=True)
        assert SIGNUP_FIELDS["phone"] not in page and SIGNUP_ADDRESS not in page

    add_quote(job_id, tradesperson, status=QUOTE_PENDING)
    page = client.get(f"/jobs/{job_id}").get_data(as_text=True)
    assert SIGNUP_FIELDS["phone"] not in page and SIGNUP_ADDRESS not in page


def test_signup_contact_data_is_shown_after_quote_is_accepted(
    client, signed_up_customer, make_tradesperson, make_job, add_quote, login
):
    tradesperson = make_tradesperson()
    job_id = make_job(signed_up_customer, status=JOB_IN_PROGRESS)
    add_quote(job_id, tradesperson)
    login("ben@example.com")

    page = client.get(f"/jobs/{job_id}").get_data(as_text=True)

    assert SIGNUP_FIELDS["phone"] in page and SIGNUP_ADDRESS in page


def test_signup_contact_data_is_shown_after_slot_is_booked(
    client, signed_up_customer, make_tradesperson, make_job, add_booking, login
):
    tradesperson = make_tradesperson()
    job_id = make_job(signed_up_customer, status=JOB_IN_PROGRESS)
    add_booking(job_id, tradesperson)
    login("ben@example.com")

    page = client.get(f"/jobs/{job_id}").get_data(as_text=True)

    assert SIGNUP_FIELDS["phone"] in page and SIGNUP_ADDRESS in page


def test_tradesperson_contact_data_appears_on_no_existing_page(
    app, client, customer_id, make_tradesperson, make_user, make_job, add_quote, login
):
    # There is no public profile or search yet, so this covers every page that shows a tradesperson today.
    tradesperson = make_tradesperson(email="ben@example.com")
    make_tradesperson(email="other@example.com")
    pending = make_tradesperson(email="new@example.com", status=VERIFICATION_PENDING)
    make_user(email="admin@example.com", role=ROLE_ADMIN)
    tradesperson_phone, tradesperson_address = "+49 40 5550777", "Werkstrasse 1, 20095 Hamburg"
    with app.app_context():
        for user_id in (tradesperson, pending):
            user = db.session.get(User, user_id)
            user.phone = tradesperson_phone
            user.address = tradesperson_address
        db.session.commit()
    job_id = make_job(customer_id, status=JOB_IN_PROGRESS)
    add_quote(job_id, tradesperson)

    visits = [
        ("anna@example.com", ["/", "/jobs/mine"]),
        ("other@example.com", ["/", "/jobs/"]),
        ("admin@example.com", ["/", "/admin/tradespeople"]),
    ]
    for email, urls in visits:
        login(email)
        for url in urls:
            response = client.get(url)
            assert response.status_code == 200
            page = response.get_data(as_text=True)
            assert tradesperson_phone not in page and tradesperson_address not in page
        client.post("/auth/logout")


# --- Job page: send a quote, or fix the one already sent ----------------------------------


def quote_id_of(app):
    with app.app_context():
        return db.session.scalar(db.select(Quote.id))


def test_job_page_offers_to_send_a_quote_when_there_is_none(client, customer_id, make_tradesperson, make_job, login):
    make_tradesperson()
    job_id = make_job(customer_id)
    login("ben@example.com")

    page = client.get(f"/jobs/{job_id}").get_data(as_text=True)

    assert f"/quotes/job/{job_id}/new" in page
    assert "Edit your quote" not in page


def test_job_page_offers_to_edit_a_pending_quote_instead(
    app, client, customer_id, make_tradesperson, make_job, add_quote, login
):
    tradesperson = make_tradesperson()
    job_id = make_job(customer_id)
    add_quote(job_id, tradesperson, status=QUOTE_PENDING)
    login("ben@example.com")

    page = client.get(f"/jobs/{job_id}").get_data(as_text=True)

    assert f"/quotes/{quote_id_of(app)}/edit" in page
    assert "Edit your quote" in page
    assert "150.00" in page  # the quote as it stands, so a typo is easy to spot
    assert "takes 2 h" in page
    assert "Send a quote" not in page


def test_edit_link_on_the_job_page_opens_the_edit_form(
    app, client, customer_id, make_tradesperson, make_job, add_quote, login
):
    tradesperson = make_tradesperson()
    job_id = make_job(customer_id)
    add_quote(job_id, tradesperson, status=QUOTE_PENDING)
    login("ben@example.com")

    response = client.get(f"/quotes/{quote_id_of(app)}/edit")

    assert response.status_code == 200


def test_corrected_quote_shows_the_new_price_on_the_job_page(
    app, client, customer_id, make_tradesperson, make_job, add_quote, login
):
    tradesperson = make_tradesperson()
    job_id = make_job(customer_id)
    add_quote(job_id, tradesperson, status=QUOTE_PENDING)
    login("ben@example.com")

    client.post(
        f"/quotes/{quote_id_of(app)}/edit",
        data={"price": "95", "earliest_start": "2030-02-01", "duration": "60", "message": ""},
    )

    page = client.get(f"/jobs/{job_id}").get_data(as_text=True)
    assert "95.00" in page and "150.00" not in page
    assert "takes 1 h" in page  # the corrected duration shows too


@pytest.mark.parametrize(
    "status, text",
    [(QUOTE_ACCEPTED, "accepted your quote"), (QUOTE_REJECTED, "chose another quote")],
)
def test_job_page_cannot_edit_a_quote_the_customer_already_decided(
    app, client, customer_id, make_tradesperson, make_job, add_quote, login, status, text
):
    tradesperson = make_tradesperson()
    # A multi-trade job stays open after one quote is decided, so the tradesperson can still open it.
    job_id = make_job(customer_id, trades=("Plumber", "Tiler"))
    add_quote(job_id, tradesperson, status=status)
    login("ben@example.com")

    page = client.get(f"/jobs/{job_id}").get_data(as_text=True)

    assert text in page
    assert "Edit your quote" not in page and "Send a quote" not in page


def test_another_tradespersons_quote_does_not_hide_my_send_button(
    client, customer_id, make_tradesperson, make_job, add_quote, login
):
    other = make_tradesperson(email="rita@example.com")
    make_tradesperson(email="ben@example.com")
    job_id = make_job(customer_id)
    add_quote(job_id, other, status=QUOTE_PENDING)
    login("ben@example.com")

    page = client.get(f"/jobs/{job_id}").get_data(as_text=True)

    assert "Send a quote" in page
    assert "Edit your quote" not in page


def test_tradesperson_cannot_edit_somebody_elses_quote_through_the_job_page_link(
    app, client, customer_id, make_tradesperson, make_job, add_quote, login
):
    other = make_tradesperson(email="rita@example.com")
    make_tradesperson(email="ben@example.com")
    job_id = make_job(customer_id)
    add_quote(job_id, other, status=QUOTE_PENDING)
    login("ben@example.com")

    # Even with the link typed by hand, the edit route keeps its own ownership check.
    assert client.get(f"/quotes/{quote_id_of(app)}/edit").status_code == 403
