from datetime import date
from html.parser import HTMLParser
from uuid import uuid4

import pytest

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
    TRADE_APPROVED,
    VERIFICATION_PENDING,
    VERIFICATION_REJECTED,
    VERIFICATION_VERIFIED,
    Job,
    JobTrade,
    Project,
    Quote,
    TradespersonProfile,
    TradespersonTrade,
)

EMAIL = "user@example.com"

VISITOR = {
    "Home": "/",
    "Sign up": "#",
    "As a customer": "/auth/signup/customer",
    "As a tradesperson": "/auth/signup/tradesperson",
    "Log in": "/auth/login",
}
UNCONFIRMED_CUSTOMER = {"Confirm your email to post a job": "/auth/confirm-email"}
CONFIRMED_CUSTOMER = {
    "Post a job": "/jobs/new",
    "My jobs": "/jobs/mine",
    "Find tradespeople": "/profiles/",
    "My appointments": "/bookings/appointments",
    "Tradespeople reviews": "/reviews/tradespeople",
}
VERIFIED_TRADESPERSON = {
    "Open jobs": "/jobs/",
    "My quotes": "/quotes/mine",
    "My availability": "/bookings/availability",
    "My profile": "/profiles/me",
    "My appointments": "/bookings/appointments",
}
ADMIN = {"Approve tradespeople and trades": "/admin/tradespeople"}

LOGGED_IN_ROLES = [
    pytest.param({"role": ROLE_CUSTOMER}, UNCONFIRMED_CUSTOMER, id="unconfirmed-customer"),
    pytest.param({"role": ROLE_CUSTOMER, "email_verified": True}, CONFIRMED_CUSTOMER, id="confirmed-customer"),
    pytest.param(
        {"role": ROLE_TRADESPERSON, "status": VERIFICATION_VERIFIED, "email_verified": True},
        VERIFIED_TRADESPERSON,
        id="verified-tradesperson",
    ),
    pytest.param({"role": ROLE_TRADESPERSON, "status": VERIFICATION_PENDING, "email_verified": True}, {}, id="pending"),
    pytest.param(
        {"role": ROLE_TRADESPERSON, "status": VERIFICATION_REJECTED, "email_verified": True}, {}, id="rejected"
    ),
    pytest.param({"role": ROLE_ADMIN, "email_verified": True}, ADMIN, id="admin"),
]


class NavbarParser(HTMLParser):
    """Collects what the navbar offers: its links, its current links, its forms and its plain text."""

    def __init__(self):
        super().__init__()
        self.in_nav = False
        self.links = {}
        self.current = []
        self.form_actions = []
        self.notices = []
        self.has_toggler = False
        self.has_collapse = False
        self.link_attrs = None
        self.in_text = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class") or ""
        if tag == "nav":
            self.in_nav = True
        if not self.in_nav:
            return
        if tag == "a" and "navbar-brand" not in classes:
            self.link_attrs = attrs
        if tag == "form":
            self.form_actions.append(attrs.get("action"))
        if tag == "button" and "navbar-toggler" in classes:
            self.has_toggler = attrs.get("data-bs-target") == "#main-nav"
        if tag == "div" and "navbar-collapse" in classes and "collapse" in classes:
            self.has_collapse = attrs.get("id") == "main-nav"
        if tag == "span" and "navbar-text" in classes:
            self.in_text = True

    def handle_data(self, data):
        if self.link_attrs is not None and data.strip():
            label = data.strip()
            self.links[label] = self.link_attrs.get("href")
            if self.link_attrs.get("aria-current") == "page":
                self.current.append(label)
            assert "disabled" not in (self.link_attrs.get("class") or "")
            self.link_attrs = None
        if self.in_text and data.strip():
            self.notices.append(data.strip())
            self.in_text = False

    def handle_endtag(self, tag):
        if tag == "nav":
            self.in_nav = False


def navbar(html):
    parser = NavbarParser()
    parser.feed(html)
    return parser


def navbar_of(client, url="/"):
    return navbar(client.get(url).get_data(as_text=True))


@pytest.fixture
def logged_in(app, client, make_user, login):
    """Creates a user with a profile if it is a tradesperson, and logs in."""

    def _logged_in(role, status=None, email_verified=False):
        user_id = make_user(email=EMAIL, role=role, status=status, email_verified=email_verified)
        if role == ROLE_TRADESPERSON:
            with app.app_context():
                profile = TradespersonProfile(
                    user_id=user_id,
                    company_name="Harbor Plumbing Ltd",
                    service_area="Berlin",
                    trades=[TradespersonTrade(trade="Plumber", status=TRADE_APPROVED)],
                )
                db.session.add(profile)
                db.session.commit()
                profile_id = profile.id
        else:
            profile_id = None
        login(EMAIL)
        return profile_id

    return _logged_in


# --- Each role sees exactly its links -----------------------------------------------------


def test_visitor_sees_exactly_its_links(client):
    assert navbar_of(client).links == VISITOR


@pytest.mark.parametrize("user, expected", LOGGED_IN_ROLES)
def test_each_role_sees_exactly_its_links(client, logged_in, user, expected):
    logged_in(**user)
    assert navbar_of(client).links == expected


def test_visitor_has_no_log_out_form(client):
    assert navbar_of(client).form_actions == []


@pytest.mark.parametrize("user, expected", LOGGED_IN_ROLES)
def test_every_logged_in_role_can_log_out(client, logged_in, user, expected):
    logged_in(**user)
    assert navbar_of(client).form_actions == ["/auth/logout"]


def test_log_out_form_has_a_csrf_token(client, logged_in):
    logged_in(role=ROLE_ADMIN)
    assert 'name="csrf_token"' in client.get("/").get_data(as_text=True).split("</nav>")[0]


def test_links_are_the_same_on_other_pages(client, logged_in):
    logged_in(role=ROLE_CUSTOMER, email_verified=True)
    assert navbar_of(client, "/jobs/mine").links == CONFIRMED_CUSTOMER


# --- Each link works for the role that sees it --------------------------------------------


def test_every_visitor_link_opens(client):
    for href in VISITOR.values():
        if href != "#":
            assert client.get(href).status_code == 200


@pytest.mark.parametrize("user, expected", LOGGED_IN_ROLES)
def test_every_link_opens_for_the_role_that_sees_it(client, logged_in, user, expected):
    logged_in(**user)
    for href in expected.values():
        response = client.get(href, follow_redirects=True)
        assert response.status_code == 200, href
        assert response.request.path != "/auth/login"


def test_links_for_customers_are_not_a_dead_end_for_unconfirmed_customers(client, logged_in):
    # Why the unconfirmed customer gets a prompt: these pages would only send them to the confirm page.
    logged_in(role=ROLE_CUSTOMER)
    for href in CONFIRMED_CUSTOMER.values():
        assert client.get(href).location.endswith("/auth/confirm-email"), href


@pytest.mark.parametrize("status", [VERIFICATION_PENDING, VERIFICATION_REJECTED])
def test_hidden_tradesperson_links_stay_blocked(client, logged_in, status):
    # Hiding a link is not access control, so the routes must still refuse.
    logged_in(role=ROLE_TRADESPERSON, status=status, email_verified=True)
    for href in VERIFIED_TRADESPERSON.values():
        assert client.get(href).status_code == 403, href


# --- Notices ------------------------------------------------------------------------------


def test_pending_tradesperson_sees_a_notice_and_no_name(client, logged_in):
    logged_in(role=ROLE_TRADESPERSON, status=VERIFICATION_PENDING, email_verified=True)
    assert navbar_of(client).notices == ["Waiting for approval"]


def test_rejected_tradesperson_sees_a_notice_and_no_name(client, logged_in):
    logged_in(role=ROLE_TRADESPERSON, status=VERIFICATION_REJECTED, email_verified=True)
    assert navbar_of(client).notices == ["Account not approved"]


def test_confirmed_customer_sees_own_name(client, logged_in):
    logged_in(role=ROLE_CUSTOMER, email_verified=True)
    assert navbar_of(client).notices == ["Test User"]


def test_no_link_is_disabled(client, logged_in):
    assert "disabled" not in client.get("/").get_data(as_text=True).split("</nav>")[0]
    logged_in(role=ROLE_CUSTOMER, email_verified=True)
    assert "disabled" not in client.get("/").get_data(as_text=True).split("</nav>")[0]


# --- The current page is marked -----------------------------------------------------------


@pytest.mark.parametrize("url, label", [("/", "Home"), ("/auth/login", "Log in")])
def test_current_page_is_marked_for_visitors(client, url, label):
    assert navbar_of(client, url).current == [label]


def test_current_signup_page_is_marked(client):
    assert navbar_of(client, "/auth/signup/customer").current == ["As a customer"]
    assert navbar_of(client, "/auth/signup/tradesperson").current == ["As a tradesperson"]


@pytest.mark.parametrize("label, href", CONFIRMED_CUSTOMER.items())
def test_current_page_is_marked_for_customers(client, logged_in, label, href):
    logged_in(role=ROLE_CUSTOMER, email_verified=True)
    assert navbar_of(client, href).current == [label]


def test_home_marks_nothing_for_logged_in_users(client, logged_in):
    logged_in(role=ROLE_CUSTOMER, email_verified=True)
    assert navbar_of(client).current == []


def test_current_page_is_marked_for_admin(client, logged_in):
    logged_in(role=ROLE_ADMIN)
    assert navbar_of(client, "/admin/tradespeople").current == list(ADMIN)


def test_current_page_is_marked_for_unconfirmed_customer(client, logged_in):
    logged_in(role=ROLE_CUSTOMER)
    assert navbar_of(client, "/auth/confirm-email").current == list(UNCONFIRMED_CUSTOMER)


@pytest.mark.parametrize(
    "label, href",
    [
        ("Open jobs", "/jobs/"),
        ("My quotes", "/quotes/mine"),
        ("My availability", "/bookings/availability"),
        ("My appointments", "/bookings/appointments"),
    ],
)
def test_current_page_is_marked_for_tradespeople(client, logged_in, label, href):
    logged_in(role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED, email_verified=True)
    assert navbar_of(client, href).current == [label]


@pytest.mark.parametrize("suffix", ["", "/edit"])
def test_own_profile_marks_my_profile(client, logged_in, suffix):
    profile_id = logged_in(role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED, email_verified=True)
    assert navbar_of(client, f"/profiles/{profile_id}{suffix}").current == ["My profile"]


def test_somebody_elses_profile_does_not_mark_my_profile(app, client, logged_in, make_user):
    logged_in(role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED, email_verified=True)
    other = make_user(email="other@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED)
    with app.app_context():
        profile = TradespersonProfile(user_id=other, company_name="Other Ltd", service_area="Berlin")
        db.session.add(profile)
        db.session.commit()
        other_profile = profile.id
    assert navbar_of(client, f"/profiles/{other_profile}").current == []


# --- Phone width --------------------------------------------------------------------------


def test_navbar_collapses_on_small_screens(client, logged_in):
    assert navbar_of(client).has_toggler
    assert navbar_of(client).has_collapse
    logged_in(role=ROLE_CUSTOMER, email_verified=True)
    assert navbar_of(client).has_toggler
    assert navbar_of(client).has_collapse


# --- Home page ----------------------------------------------------------------------------


def main_links(client):
    html = client.get("/").get_data(as_text=True).split("<main", 1)[1]
    parser = NavbarParser()
    parser.in_nav = True
    parser.feed(html)
    return parser.links


def test_home_page_offers_signup_to_visitors(client):
    assert main_links(client) == {
        "I need a tradesperson": "/auth/signup/customer",
        "I am a tradesperson": "/auth/signup/tradesperson",
    }


@pytest.mark.parametrize(
    "user, expected",
    [
        pytest.param({"role": ROLE_CUSTOMER}, UNCONFIRMED_CUSTOMER, id="unconfirmed-customer"),
        pytest.param(
            {"role": ROLE_CUSTOMER, "email_verified": True},
            {"Post a job": "/jobs/new", "Find tradespeople": "/profiles/"},
            id="confirmed-customer",
        ),
        pytest.param(
            {"role": ROLE_TRADESPERSON, "status": VERIFICATION_VERIFIED, "email_verified": True},
            {"Open jobs": "/jobs/", "My quotes": "/quotes/mine", "My availability": "/bookings/availability"},
            id="verified-tradesperson",
        ),
        pytest.param({"role": ROLE_TRADESPERSON, "status": VERIFICATION_PENDING, "email_verified": True}, {}, id="pending"),
        pytest.param({"role": ROLE_ADMIN}, ADMIN, id="admin"),
    ],
)
def test_home_page_offers_each_role_its_next_step(client, logged_in, user, expected):
    logged_in(**user)
    assert main_links(client) == expected


# --- Links on My jobs ---------------------------------------------------------------------


CUSTOMER = "anna@example.com"
PROJECT_LABEL = "Project page"
REVIEW_LABEL = "Mark as done and review"
DONE_LABEL = "Reviews"


@pytest.fixture
def customer_id(make_user, login):
    user_id = make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=True)
    login(CUSTOMER)
    return user_id


@pytest.fixture
def make_job(app, make_user):
    """Creates a job. `accepted` lists the trades that have an accepted quote. Returns the job id."""

    def _make(customer, trades=("Plumber",), accepted=(), status=JOB_OPEN):
        worker = make_user(
            email=f"worker{uuid4().hex}@example.com", role=ROLE_TRADESPERSON, status=VERIFICATION_VERIFIED
        )
        with app.app_context():
            job = Job(
                customer_id=customer,
                title="Test job",
                city="Berlin",
                description="Test",
                urgency="normal",
                status=status,
                trades=[JobTrade(trade=trade) for trade in trades],
            )
            db.session.add(job)
            db.session.flush()
            for job_trade in job.trades:
                if job_trade.trade in accepted:
                    db.session.add(
                        Quote(
                            job_trade_id=job_trade.id,
                            tradesperson_id=worker,
                            price_cents=100,
                            earliest_start=date(2031, 1, 6),
                            status=QUOTE_ACCEPTED,
                        )
                    )
            db.session.commit()
            return job.id

    return _make


def my_jobs_links(client):
    html = client.get("/jobs/mine").get_data(as_text=True).split("<main", 1)[1]
    parser = NavbarParser()
    parser.in_nav = True
    parser.feed(html)
    return parser.links


@pytest.mark.parametrize(
    "fields, expected",
    [
        pytest.param({}, set(), id="open"),
        pytest.param({"status": JOB_CANCELLED}, set(), id="cancelled"),
        pytest.param({"accepted": ("Plumber",), "status": JOB_IN_PROGRESS}, {REVIEW_LABEL}, id="accepted"),
        pytest.param({"status": JOB_IN_PROGRESS}, {REVIEW_LABEL}, id="booked"),
        pytest.param({"accepted": ("Plumber",), "status": JOB_DONE}, {DONE_LABEL}, id="done"),
        pytest.param({"status": JOB_DONE}, set(), id="done-without-accepted-quote"),
        pytest.param(
            {"trades": ("Plumber", "Tiler"), "accepted": ("Plumber",)},
            set(),
            id="multi-trade-one-accepted",
        ),
        pytest.param(
            {"trades": ("Plumber", "Tiler"), "accepted": ("Plumber", "Tiler"), "status": JOB_IN_PROGRESS},
            {PROJECT_LABEL, REVIEW_LABEL},
            id="multi-trade-both-accepted",
        ),
        pytest.param(
            {"trades": ("Plumber", "Tiler"), "accepted": ("Plumber", "Tiler"), "status": JOB_DONE},
            {PROJECT_LABEL, DONE_LABEL},
            id="multi-trade-done",
        ),
    ],
)
def test_my_jobs_shows_the_links_for_each_state(client, customer_id, make_job, fields, expected):
    make_job(customer_id, **fields)
    labels = set(my_jobs_links(client)) & {PROJECT_LABEL, REVIEW_LABEL, DONE_LABEL}
    assert labels == expected


def test_review_link_opens_the_page_of_that_job(client, customer_id, make_job):
    job_id = make_job(customer_id, accepted=("Plumber",), status=JOB_IN_PROGRESS)
    href = my_jobs_links(client)[REVIEW_LABEL]
    assert href == f"/reviews/jobs/{job_id}"
    assert client.get(href).status_code == 200


def test_done_link_opens_the_review_form(client, customer_id, make_job):
    make_job(customer_id, accepted=("Plumber",), status=JOB_DONE)
    page = client.get(my_jobs_links(client)[DONE_LABEL])
    assert page.status_code == 200
    assert "Submit review" in page.get_data(as_text=True)


def test_project_link_leads_to_the_project(client, customer_id, make_job):
    job_id = make_job(customer_id, trades=("Plumber", "Tiler"), accepted=("Plumber", "Tiler"), status=JOB_IN_PROGRESS)
    href = my_jobs_links(client)[PROJECT_LABEL]
    assert href == f"/projects/job/{job_id}"
    response = client.get(href, follow_redirects=True)
    assert response.status_code == 200
    assert response.request.path.startswith("/projects/")


def test_opening_my_jobs_creates_no_project(app, client, customer_id, make_job):
    make_job(customer_id, trades=("Plumber", "Tiler"), accepted=("Plumber", "Tiler"), status=JOB_IN_PROGRESS)
    client.get("/jobs/mine")
    with app.app_context():
        assert db.session.query(Project).count() == 0


def test_my_jobs_does_not_link_other_customers_jobs(client, customer_id, make_job, make_user):
    other = make_user(email="other@example.com", role=ROLE_CUSTOMER, email_verified=True)
    job_id = make_job(other, accepted=("Plumber",), status=JOB_IN_PROGRESS)
    assert f"/reviews/jobs/{job_id}" not in client.get("/jobs/mine").get_data(as_text=True)


def test_review_page_of_another_customers_job_stays_blocked(client, customer_id, make_job, make_user):
    other = make_user(email="other@example.com", role=ROLE_CUSTOMER, email_verified=True)
    job_id = make_job(other, accepted=("Plumber",), status=JOB_IN_PROGRESS)
    assert client.get(f"/reviews/jobs/{job_id}").status_code == 403


# --- Project link on the job page ---------------------------------------------------------


MEMBER = "member@example.com"
SECOND_MEMBER = "second@example.com"
OUTSIDER = "outsider@example.com"


@pytest.fixture
def make_person(app, make_user):
    def _make(email, trades, status=VERIFICATION_VERIFIED):
        user_id = make_user(email=email, role=ROLE_TRADESPERSON, status=status, email_verified=True)
        with app.app_context():
            db.session.add(
                TradespersonProfile(
                    user_id=user_id,
                    company_name=f"{email} Ltd",
                    service_area="Berlin",
                    trades=[TradespersonTrade(trade=trade, status=TRADE_APPROVED) for trade in trades],
                )
            )
            db.session.commit()
        return user_id

    return _make


@pytest.fixture
def make_quoted_job(app):
    """`quotes` maps a trade to (tradesperson id, quote status). Returns the job id."""

    def _make(customer, trades, quotes, status=JOB_OPEN):
        with app.app_context():
            job = Job(
                customer_id=customer,
                title="Test job",
                city="Berlin",
                description="Test",
                urgency="normal",
                status=status,
                trades=[JobTrade(trade=trade) for trade in trades],
            )
            db.session.add(job)
            db.session.flush()
            for job_trade in job.trades:
                if job_trade.trade in quotes:
                    person, quote_status = quotes[job_trade.trade]
                    db.session.add(
                        Quote(
                            job_trade_id=job_trade.id,
                            tradesperson_id=person,
                            price_cents=100,
                            earliest_start=date(2031, 1, 6),
                            status=quote_status,
                        )
                    )
            db.session.commit()
            return job.id

    return _make


@pytest.fixture
def project_job(make_user, make_person, make_quoted_job):
    """A job with two accepted trades. A third trade keeps it open, so an outsider can still open its page."""
    customer = make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=True)
    member = make_person(MEMBER, ["Plumber"])
    second = make_person(SECOND_MEMBER, ["Tiler"])
    make_person(OUTSIDER, ["Painter"])
    return make_quoted_job(
        customer,
        ("Plumber", "Tiler", "Painter"),
        {"Plumber": (member, QUOTE_ACCEPTED), "Tiler": (second, QUOTE_ACCEPTED)},
    )


def job_page_links(client, job_id):
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    parser = NavbarParser()
    parser.in_nav = True
    parser.feed(response.get_data(as_text=True).split("<main", 1)[1])
    return parser.links


@pytest.mark.parametrize("email", [MEMBER, SECOND_MEMBER])
def test_tradesperson_with_access_sees_the_project_link(client, project_job, login, email):
    login(email)
    assert job_page_links(client, project_job)["Project page"] == f"/projects/job/{project_job}"


@pytest.mark.parametrize("email", [MEMBER, SECOND_MEMBER])
def test_project_link_opens_for_a_member(client, project_job, login, email):
    login(email)
    response = client.get(f"/projects/job/{project_job}", follow_redirects=True)
    assert response.status_code == 403


def test_tradesperson_without_access_sees_no_project_link(client, project_job, login):
    login(OUTSIDER)
    assert "Project page" not in job_page_links(client, project_job)


def test_tradesperson_with_a_pending_quote_sees_no_project_link(
    client, make_user, make_person, make_quoted_job, login
):
    customer = make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=True)
    member = make_person(MEMBER, ["Plumber"])
    second = make_person(SECOND_MEMBER, ["Tiler"])
    waiting = make_person(OUTSIDER, ["Painter"])
    job_id = make_quoted_job(
        customer,
        ("Plumber", "Tiler", "Painter"),
        {"Plumber": (member, QUOTE_ACCEPTED), "Tiler": (second, QUOTE_ACCEPTED), "Painter": (waiting, QUOTE_PENDING)},
    )
    login(OUTSIDER)
    assert "Project page" not in job_page_links(client, job_id)


def test_tradesperson_with_a_rejected_quote_sees_no_project_link(
    client, make_user, make_person, make_quoted_job, login
):
    customer = make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=True)
    member = make_person(MEMBER, ["Plumber"])
    second = make_person(SECOND_MEMBER, ["Tiler"])
    rejected = make_person(OUTSIDER, ["Painter", "Plumber"])
    job_id = make_quoted_job(
        customer,
        ("Plumber", "Tiler", "Painter"),
        {"Plumber": (member, QUOTE_ACCEPTED), "Tiler": (second, QUOTE_ACCEPTED), "Painter": (rejected, QUOTE_REJECTED)},
    )
    login(OUTSIDER)
    assert "Project page" not in job_page_links(client, job_id)


def test_only_one_accepted_trade_means_no_project_yet(client, make_user, make_person, make_quoted_job, login):
    # With one accepted trade the project route answers 404, so the link would lead nowhere.
    customer = make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=True)
    member = make_person(MEMBER, ["Plumber"])
    job_id = make_quoted_job(customer, ("Plumber", "Tiler"), {"Plumber": (member, QUOTE_ACCEPTED)})
    login(MEMBER)
    assert "Project page" not in job_page_links(client, job_id)
    assert client.get(f"/projects/job/{job_id}").status_code == 403


def test_single_trade_job_has_no_project_link(client, make_user, make_person, make_quoted_job, login):
    customer = make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=True)
    member = make_person(MEMBER, ["Plumber"])
    job_id = make_quoted_job(customer, ("Plumber",), {"Plumber": (member, QUOTE_ACCEPTED)}, status=JOB_IN_PROGRESS)
    login(MEMBER)
    assert "Project page" not in job_page_links(client, job_id)


def test_member_keeps_the_link_when_the_job_is_in_progress(client, make_user, make_person, make_quoted_job, login):
    customer = make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=True)
    member = make_person(MEMBER, ["Plumber"])
    second = make_person(SECOND_MEMBER, ["Tiler"])
    job_id = make_quoted_job(
        customer,
        ("Plumber", "Tiler"),
        {"Plumber": (member, QUOTE_ACCEPTED), "Tiler": (second, QUOTE_ACCEPTED)},
        status=JOB_IN_PROGRESS,
    )
    login(MEMBER)
    assert "Project page" in job_page_links(client, job_id)


def test_visitor_cannot_open_the_job_page_or_see_the_link(client, project_job):
    response = client.get(f"/jobs/{project_job}")
    assert response.status_code == 302
    assert "/auth/login" in response.location
    assert "Project page" not in response.get_data(as_text=True)


def test_customer_of_another_job_cannot_open_the_job_page_or_see_the_link(client, project_job, make_user, login):
    make_user(email="other@example.com", role=ROLE_CUSTOMER, email_verified=True)
    login("other@example.com")
    response = client.get(f"/jobs/{project_job}")
    assert response.status_code == 403
    assert "Project page" not in response.get_data(as_text=True)


def test_customer_of_this_job_sees_no_link_on_this_page(client, project_job, login):
    # Customers have their own link on My jobs; the tradesperson job page is not for them.
    login(CUSTOMER)
    response = client.get(f"/jobs/{project_job}")
    assert response.status_code == 403
    assert "Project page" not in response.get_data(as_text=True)


@pytest.mark.parametrize("status", [VERIFICATION_PENDING, VERIFICATION_REJECTED])
def test_unverified_tradesperson_with_accepted_quote_sees_no_link(
    client, make_user, make_person, make_quoted_job, login, status
):
    customer = make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=True)
    member = make_person(MEMBER, ["Plumber"], status=status)
    second = make_person(SECOND_MEMBER, ["Tiler"])
    job_id = make_quoted_job(
        customer, ("Plumber", "Tiler"), {"Plumber": (member, QUOTE_ACCEPTED), "Tiler": (second, QUOTE_ACCEPTED)}
    )
    login(MEMBER)
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 403
    assert "Project page" not in response.get_data(as_text=True)


@pytest.mark.parametrize("url", ["/quotes/mine", "/bookings/appointments"])
def test_other_tradesperson_pages_lead_to_the_job_page_or_stay_empty(client, project_job, login, url):
    # My quotes links an accepted quote to the job page, so the one link there is enough.
    login(MEMBER)
    page = client.get(url).get_data(as_text=True)
    if url == "/quotes/mine":
        assert f"/jobs/{project_job}" in page
    assert "/projects/" not in page
