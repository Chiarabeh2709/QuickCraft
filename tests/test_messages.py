from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import (
    JOB_CANCELLED,
    JOB_DONE,
    JOB_IN_PROGRESS,
    QUOTE_REJECTED,
    ROLE_ADMIN,
    ROLE_TRADESPERSON,
    TRADE_APPROVED,
    VERIFICATION_PENDING,
    VERIFICATION_REJECTED,
    VERIFICATION_VERIFIED,
    Booking,
    Job,
    JobTrade,
    Message,
    MessageThread,
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
ADDRESS = "Hauptstrasse 12, Berlin"
START = date.today() + timedelta(days=10)


# --- Helpers ------------------------------------------------------------------------------


@pytest.fixture
def customer_id(app, make_user):
    user_id = make_user(email=ANNA, email_verified=True)
    with app.app_context():
        user = db.session.get(User, user_id)
        user.name = "Anna Schmidt"
        user.phone = PHONE
        user.address = ADDRESS
        db.session.commit()
    return user_id


@pytest.fixture
def make_tradesperson(app, make_user):
    def _make(email=BEN, status=VERIFICATION_VERIFIED):
        user_id = make_user(email=email, role=ROLE_TRADESPERSON, status=status, email_verified=True)
        with app.app_context():
            db.session.add(
                TradespersonProfile(
                    user_id=user_id,
                    company_name=f"{email.split('@')[0]} GmbH",
                    service_area="Berlin",
                    trades=[TradespersonTrade(trade="Plumber", status=TRADE_APPROVED)],
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
def add_quote(app):
    def _add(job, tradesperson):
        with app.app_context():
            job_trade = db.session.scalar(db.select(JobTrade).filter_by(job_id=job))
            quote = Quote(job_trade_id=job_trade.id, tradesperson_id=tradesperson, price_cents=15000, earliest_start=START)
            db.session.add(quote)
            db.session.commit()
            return quote.id

    return _add


@pytest.fixture
def ben(job_id, make_tradesperson, add_quote):
    """A verified tradesperson with a quote on the job."""
    user_id = make_tradesperson()
    add_quote(job_id, user_id)
    return user_id


def url(job, tradesperson, action=""):
    return f"/messages/job/{job}/tradesperson/{tradesperson}{action}"


def send(client, job, tradesperson, body="Hello", **options):
    return client.post(url(job, tradesperson), data={"body": body}, **options)


def text_of(response):
    return response.get_data(as_text=True)


def switch_to(client, login, email):
    client.post("/auth/logout")
    login(email)


def message_count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Message.id)))


def thread_count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(MessageThread.id)))


def set_job_status(app, job, status):
    with app.app_context():
        db.session.get(Job, job).status = status
        db.session.commit()


@pytest.fixture
def thread(client, job_id, ben, login):
    """A thread started by Anna, who stays logged in."""
    login(ANNA)
    send(client, job_id, ben)


# --- Threads ------------------------------------------------------------------------------


def test_first_message_creates_the_thread(app, client, job_id, ben, login):
    login(ANNA)
    assert thread_count(app) == 0

    response = send(client, job_id, ben, "Is Monday possible?", follow_redirects=True)

    assert "Is Monday possible?" in text_of(response)
    assert thread_count(app) == 1


def test_opening_a_conversation_creates_no_thread(app, client, job_id, ben, login):
    login(ANNA)

    assert client.get(url(job_id, ben)).status_code == 200
    assert thread_count(app) == 0


def test_both_sides_write_in_the_same_thread(app, client, thread, job_id, ben, login):
    switch_to(client, login, BEN)

    response = send(client, job_id, ben, "Yes, it is.", follow_redirects=True)

    assert "Hello" in text_of(response) and "Yes, it is." in text_of(response)
    assert thread_count(app) == 1


def test_each_tradesperson_has_their_own_thread(app, client, thread, job_id, make_tradesperson, add_quote, login):
    cara = make_tradesperson(email=CARA)
    add_quote(job_id, cara)
    send(client, job_id, cara, "Only for Cara")

    assert thread_count(app) == 2
    assert "Only for Cara" not in text_of(client.get(url(job_id, thread_id_ben(app))))


def thread_id_ben(app):
    with app.app_context():
        return db.session.scalar(db.select(User.id).filter_by(email=BEN))


def test_booking_is_enough_to_take_part(app, client, job_id, make_tradesperson, login):
    tradesperson = make_tradesperson()
    with app.app_context():
        db.session.add(Booking(job_id=job_id, tradesperson_id=tradesperson, starts_at=datetime(2030, 1, 7, 9)))
        db.session.commit()
    login(BEN)

    send(client, job_id, tradesperson)

    assert message_count(app) == 1


def test_rejected_quote_still_counts_as_sent(app, client, job_id, ben, login):
    with app.app_context():
        db.session.scalar(db.select(Quote)).status = QUOTE_REJECTED
        db.session.commit()
    login(BEN)

    send(client, job_id, ben)

    assert message_count(app) == 1


# --- Access -------------------------------------------------------------------------------


def test_logged_out_visitor_is_sent_to_login(app, client, job_id, ben):
    assert "/auth/login" in client.get(url(job_id, ben)).location
    assert "/auth/login" in send(client, job_id, ben).location
    assert "/auth/login" in client.get("/messages/").location
    assert "/auth/login" in client.post(url(job_id, ben, "/share-phone")).location
    assert message_count(app) == 0


def test_other_customer_gets_403(app, client, thread, job_id, ben, make_user, login):
    make_user(email=OTHER, email_verified=True)
    switch_to(client, login, OTHER)

    assert client.get(url(job_id, ben)).status_code == 403
    assert send(client, job_id, ben).status_code == 403
    assert message_count(app) == 1


def test_other_tradesperson_gets_403(app, client, thread, job_id, ben, make_tradesperson, add_quote, login):
    # A quote on the same job gives access to their own thread, never to another one.
    add_quote(job_id, make_tradesperson(email=CARA))
    switch_to(client, login, CARA)

    assert client.get(url(job_id, ben)).status_code == 403
    assert send(client, job_id, ben).status_code == 403


def test_tradesperson_without_quote_or_booking_gets_403(app, client, job_id, make_tradesperson, login):
    outsider = make_tradesperson(email=CARA)
    login(CARA)

    assert client.get(url(job_id, outsider)).status_code == 403
    assert send(client, job_id, outsider).status_code == 403
    assert thread_count(app) == 0


@pytest.mark.parametrize("status", [VERIFICATION_PENDING, VERIFICATION_REJECTED])
def test_unverified_tradesperson_gets_403(app, client, job_id, make_tradesperson, add_quote, login, status):
    tradesperson = make_tradesperson(email=CARA, status=status)
    add_quote(job_id, tradesperson)
    login(CARA)

    assert client.get(url(job_id, tradesperson)).status_code == 403
    assert send(client, job_id, tradesperson).status_code == 403
    assert client.get("/messages/").status_code == 403
    assert thread_count(app) == 0


def test_customer_cannot_start_a_thread_with_an_unrelated_tradesperson(app, client, job_id, make_tradesperson, login):
    outsider = make_tradesperson(email=CARA)
    login(ANNA)

    assert send(client, job_id, outsider).status_code == 403
    assert thread_count(app) == 0


def test_admin_gets_403(client, thread, job_id, ben, make_user, login):
    make_user(email=OTHER, role=ROLE_ADMIN, email_verified=True)
    switch_to(client, login, OTHER)

    assert client.get(url(job_id, ben)).status_code == 403
    assert client.get("/messages/").status_code == 403


def test_withdrawn_quote_ends_the_tradespersons_access(app, client, thread, job_id, ben, login):
    with app.app_context():
        db.session.delete(db.session.scalar(db.select(Quote)))
        db.session.commit()
    switch_to(client, login, BEN)

    assert client.get(url(job_id, ben)).status_code == 403
    assert "Leaky tap" not in text_of(client.get("/messages/"))


def test_unknown_job_or_tradesperson_is_not_found(client, job_id, ben, customer_id, login):
    login(ANNA)

    assert client.get(url(999, ben)).status_code == 404
    assert client.get(url(job_id, 999)).status_code == 404
    assert client.get(url(job_id, customer_id)).status_code == 404


@pytest.mark.parametrize("action", ["", "/share-phone", "/revoke-phone"])
def test_every_form_checks_the_csrf_token(app, client, thread, job_id, ben, action):
    app.config["WTF_CSRF_ENABLED"] = True

    assert client.post(url(job_id, ben, action), data={"body": "Hi"}).status_code == 400
    assert message_count(app) == 1


# --- Message text -------------------------------------------------------------------------


def test_html_in_a_message_is_escaped(client, job_id, ben, login):
    login(ANNA)

    page = text_of(send(client, job_id, ben, "<script>alert(1)</script>", follow_redirects=True))

    assert "<script>alert(1)" not in page
    assert "&lt;script&gt;alert(1)" in page


def test_html_is_escaped_on_the_job_page_too(client, job_id, ben, login):
    login(ANNA)
    send(client, job_id, ben, "<b>bold</b>")
    switch_to(client, login, BEN)

    page = text_of(client.get(f"/jobs/{job_id}"))

    assert "<b>bold</b>" not in page
    assert "&lt;b&gt;bold&lt;/b&gt;" in page


@pytest.mark.parametrize("body", ["", "   "])
def test_empty_message_is_refused(app, client, job_id, ben, login, body):
    login(ANNA)

    assert "Please write a message." in text_of(send(client, job_id, ben, body))
    assert message_count(app) == 0


def test_message_of_the_maximum_length_is_allowed(app, client, job_id, ben, login):
    login(ANNA)

    send(client, job_id, ben, "x" * 1000)

    assert message_count(app) == 1


def test_too_long_message_is_refused_and_kept_in_the_form(app, client, job_id, ben, login):
    login(ANNA)

    page = text_of(send(client, job_id, ben, "x" * 1001))

    assert "at most 1000 characters" in page
    assert "x" * 1001 in page
    assert message_count(app) == 0
    assert thread_count(app) == 0


def test_rate_limit_stops_the_eleventh_message(app, client, job_id, ben, login):
    login(ANNA)
    for number in range(10):
        send(client, job_id, ben, f"Message {number}")

    page = text_of(send(client, job_id, ben, "One too many"))

    assert "at most 10 messages per minute" in page
    assert "One too many" in page
    assert message_count(app) == 10


def test_rate_limit_counts_each_user_on_their_own(app, client, job_id, ben, login):
    login(ANNA)
    for number in range(10):
        send(client, job_id, ben, f"Message {number}")
    switch_to(client, login, BEN)

    send(client, job_id, ben, "Still possible")

    assert message_count(app) == 11


def test_rate_limit_ends_after_a_minute(app, client, job_id, ben, login):
    login(ANNA)
    for number in range(10):
        send(client, job_id, ben, f"Message {number}")
    with app.app_context():
        for message in db.session.scalars(db.select(Message)):
            message.created_at = message.created_at - timedelta(minutes=2)
        db.session.commit()

    send(client, job_id, ben, "Later")

    assert message_count(app) == 11


# --- Lists, unread and names --------------------------------------------------------------


def test_inbox_shows_a_new_message_until_it_is_opened(client, thread, job_id, ben, login):
    switch_to(client, login, BEN)
    assert "1 new" in text_of(client.get("/messages/"))

    client.get(url(job_id, ben))

    assert "1 new" not in text_of(client.get("/messages/"))


def test_job_page_counts_as_seen_for_the_tradesperson(client, thread, job_id, ben, login):
    switch_to(client, login, BEN)

    client.get(f"/jobs/{job_id}")

    assert "1 new" not in text_of(client.get("/messages/"))


def test_own_messages_are_never_new(client, thread):
    assert "1 new" not in text_of(client.get("/messages/"))


def test_customer_sees_the_reply_as_new_on_the_job_page(client, thread, job_id, ben, login):
    switch_to(client, login, BEN)
    send(client, job_id, ben, "Reply")
    switch_to(client, login, ANNA)

    assert "1 new" in text_of(client.get(f"/jobs/mine/{job_id}"))
    assert "1 new" in text_of(client.get("/messages/"))


def test_inbox_only_lists_own_threads(client, thread, make_user, login):
    make_user(email=OTHER, email_verified=True)
    switch_to(client, login, OTHER)

    assert "Leaky tap" not in text_of(client.get("/messages/"))


def test_customer_job_page_lists_who_they_can_message(client, job_id, ben, login):
    login(ANNA)

    page = text_of(client.get(f"/jobs/mine/{job_id}"))

    assert "ben GmbH" in page
    assert url(job_id, ben) in page


def test_thread_shows_company_name_and_first_name_only(client, thread, job_id, ben, login):
    switch_to(client, login, BEN)
    send(client, job_id, ben, "Reply")

    for email in (BEN, ANNA):
        switch_to(client, login, email)
        # Only the main part: the navbar shows the logged-in user's own full name.
        page = text_of(client.get(url(job_id, ben))).split("<main")[-1]
        assert "ben GmbH" in page
        assert "Anna" in page
        assert "Schmidt" not in page
        assert "Test User" not in page


def test_tradesperson_sees_the_thread_on_the_job_page(client, thread, job_id, login):
    switch_to(client, login, BEN)

    page = text_of(client.get(f"/jobs/{job_id}"))

    assert "Hello" in page
    assert "Your message" in page


def test_job_page_shows_no_thread_to_a_tradesperson_without_a_quote(client, thread, job_id, make_tradesperson, login):
    make_tradesperson(email=CARA)
    switch_to(client, login, CARA)

    page = text_of(client.get(f"/jobs/{job_id}"))

    assert "Hello" not in page
    assert "Your message" not in page


# --- Sharing the phone number -------------------------------------------------------------


def share(client, job, tradesperson):
    return client.post(url(job, tradesperson, "/share-phone"))


def revoke(client, job, tradesperson):
    return client.post(url(job, tradesperson, "/revoke-phone"))


def test_phone_stays_hidden_without_consent(client, thread, job_id, ben, login):
    switch_to(client, login, BEN)

    assert PHONE not in text_of(client.get(url(job_id, ben)))


def test_consent_shows_the_number_on_the_conversation_page(client, thread, job_id, ben, login):
    share(client, job_id, ben)
    switch_to(client, login, BEN)

    assert PHONE in text_of(client.get(url(job_id, ben)))


def test_consent_never_shows_the_address(client, thread, job_id, ben, login):
    share(client, job_id, ben)
    switch_to(client, login, BEN)

    assert ADDRESS not in text_of(client.get(url(job_id, ben)))
    assert ADDRESS not in text_of(client.get(f"/jobs/{job_id}"))


def test_consent_never_puts_the_number_on_the_job_page_or_list(client, thread, job_id, ben, login):
    share(client, job_id, ben)
    switch_to(client, login, BEN)

    assert PHONE not in text_of(client.get(f"/jobs/{job_id}"))
    assert PHONE not in text_of(client.get("/jobs/"))
    assert PHONE not in text_of(client.get("/messages/"))


def test_consent_is_for_the_chosen_tradesperson_only(client, thread, job_id, ben, make_tradesperson, add_quote, login):
    cara = make_tradesperson(email=CARA)
    add_quote(job_id, cara)
    send(client, job_id, cara, "Hello Cara")
    share(client, job_id, ben)
    switch_to(client, login, CARA)

    assert PHONE not in text_of(client.get(url(job_id, cara)))
    assert PHONE not in text_of(client.get(f"/jobs/{job_id}"))


def test_revoking_hides_the_number_again(client, thread, job_id, ben, login):
    share(client, job_id, ben)
    revoke(client, job_id, ben)
    switch_to(client, login, BEN)

    assert PHONE not in text_of(client.get(url(job_id, ben)))


def test_number_can_be_shared_again_after_revoking(client, thread, job_id, ben, login):
    share(client, job_id, ben)
    revoke(client, job_id, ben)
    share(client, job_id, ben)
    switch_to(client, login, BEN)

    assert PHONE in text_of(client.get(url(job_id, ben)))


def test_customer_sees_the_right_button(client, thread, job_id, ben):
    assert "Share my phone number" in text_of(client.get(url(job_id, ben)))
    share(client, job_id, ben)

    page = text_of(client.get(url(job_id, ben)))

    assert "Stop sharing my phone number" in page
    assert "Share my phone number" not in page


def test_tradesperson_cannot_give_or_revoke_consent(app, client, thread, job_id, ben, login):
    switch_to(client, login, BEN)

    assert share(client, job_id, ben).status_code == 403
    assert revoke(client, job_id, ben).status_code == 403
    assert "Share my phone number" not in text_of(client.get(url(job_id, ben)))


def test_other_customer_cannot_give_or_revoke_consent(client, thread, job_id, ben, make_user, login):
    make_user(email=OTHER, email_verified=True)
    switch_to(client, login, OTHER)

    assert share(client, job_id, ben).status_code == 403
    assert revoke(client, job_id, ben).status_code == 403


def test_consent_needs_an_existing_thread(app, client, job_id, ben, login):
    login(ANNA)

    assert share(client, job_id, ben).status_code == 404
    assert thread_count(app) == 0


def test_accepted_quote_still_shows_contact_data_on_the_job_page(app, client, thread, job_id, login):
    with app.app_context():
        db.session.scalar(db.select(Quote)).status = "accepted"
        db.session.commit()
    switch_to(client, login, BEN)

    assert PHONE in text_of(client.get(f"/jobs/{job_id}"))


# --- Closed jobs --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [JOB_CANCELLED, JOB_DONE])
@pytest.mark.parametrize("email", [ANNA, BEN])
def test_thread_is_read_only_on_a_closed_job(app, client, thread, job_id, ben, login, status, email):
    set_job_status(app, job_id, status)
    switch_to(client, login, email)

    page = text_of(client.get(url(job_id, ben)))
    response = send(client, job_id, ben, "Too late", follow_redirects=True)

    assert "Hello" in page
    assert "read-only" in page
    assert "Your message" not in page
    assert "read-only" in text_of(response)
    assert message_count(app) == 1


def test_running_job_stays_writable(app, client, thread, job_id, ben):
    set_job_status(app, job_id, JOB_IN_PROGRESS)

    send(client, job_id, ben, "Still fine")

    assert message_count(app) == 2


@pytest.mark.parametrize("status", [JOB_CANCELLED, JOB_DONE])
def test_number_cannot_be_shared_on_a_closed_job(app, client, thread, job_id, ben, login, status):
    set_job_status(app, job_id, status)

    share(client, job_id, ben)
    switch_to(client, login, BEN)

    assert PHONE not in text_of(client.get(url(job_id, ben)))


def test_shared_number_is_hidden_once_the_job_is_closed(app, client, thread, job_id, ben, login):
    share(client, job_id, ben)
    set_job_status(app, job_id, JOB_CANCELLED)
    switch_to(client, login, BEN)

    assert PHONE not in text_of(client.get(url(job_id, ben)))


def test_consent_can_still_be_revoked_on_a_closed_job(app, client, thread, job_id, ben):
    share(client, job_id, ben)
    set_job_status(app, job_id, JOB_DONE)

    assert "Stop sharing my phone number" in text_of(client.get(url(job_id, ben)))
    revoke(client, job_id, ben)

    with app.app_context():
        assert db.session.scalar(db.select(MessageThread)).phone_shared_at is None


def test_consent_can_still_be_revoked_after_the_quote_is_gone(app, client, thread, job_id, ben):
    share(client, job_id, ben)
    with app.app_context():
        db.session.delete(db.session.scalar(db.select(Quote)))
        db.session.commit()

    assert client.get(url(job_id, ben)).status_code == 200
    revoke(client, job_id, ben)

    with app.app_context():
        assert db.session.scalar(db.select(MessageThread)).phone_shared_at is None
