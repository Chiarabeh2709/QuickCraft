from datetime import date, time, timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    ROLE_ADMIN,
    ROLE_CUSTOMER,
    ROLE_TRADESPERSON,
    VERIFICATION_PENDING,
    VERIFICATION_REJECTED,
    VERIFICATION_VERIFIED,
    TRADE_APPROVED,
    TradespersonProfile,
    TradespersonTrade,
    AvailabilitySlot,
    Job,
    Review,
    User,
)
from config import TestConfig
from tests.conftest import PASSWORD

CUSTOMER = "anna@example.com"
OWNER = "ben@example.com"
OTHER = "tina@example.com"

# Private details of every test tradesperson. None of these may ever appear on a page.
PRIVATE_VALUES = {
    "phone": "+49 40 5550777",
    "address": "Werkstrasse 1, 20095 Hamburg",
    "name": "Private Person",
    "qualification_reference": "HWK-SECRET-1",
}


@pytest.fixture
def make_tradesperson(app, make_user):
    """Creates a tradesperson with a profile and returns the profile id."""

    def _make(
        email=OWNER,
        company="Ben Plumbing",
        trades=("Plumber",),
        area="Berlin",
        status=VERIFICATION_VERIFIED,
        description="Fast and friendly.",
    ):
        user_id = make_user(email=email, role=ROLE_TRADESPERSON, status=status, email_verified=True)
        with app.app_context():
            user = db.session.get(User, user_id)
            user.phone = PRIVATE_VALUES["phone"]
            user.address = PRIVATE_VALUES["address"]
            user.name = PRIVATE_VALUES["name"]
            profile = TradespersonProfile(
                user_id=user_id,
                company_name=company,
                service_area=area,
                description=description,
                qualification_reference=PRIVATE_VALUES["qualification_reference"],
                trades=[TradespersonTrade(trade=trade, status=TRADE_APPROVED) for trade in trades],
            )
            db.session.add(profile)
            db.session.commit()
            return profile.id

    return _make


@pytest.fixture
def customer(make_user, login):
    make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=True)
    login(CUSTOMER)


def search_page(client, **params):
    return client.get("/profiles/", query_string=params).get_data(as_text=True)


def assert_sent_to_login(response):
    assert response.status_code == 302
    assert "/auth/login" in response.location


def saved_profile(app, profile_id):
    with app.app_context():
        profile = db.session.get(TradespersonProfile, profile_id)
        return {
            "description": profile.description,
            "service_area": profile.service_area,
            "trades": sorted(entry.trade for entry in profile.trades),
            "company_name": profile.company_name,
            "qualification_reference": profile.qualification_reference,
        }


def edit_form(**changes):
    return {"description": "New text", "service_area": "Potsdam", "trades": ["Plumber"], **changes}


# --- 1) Who can search --------------------------------------------------------------------


def test_confirmed_customer_can_open_search(client, customer):
    assert client.get("/profiles/").status_code == 200


def test_anonymous_visitor_cannot_search(client):
    assert_sent_to_login(client.get("/profiles/"))


def test_unconfirmed_customer_is_sent_to_confirm_page(client, make_user, login):
    make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=False)
    login(CUSTOMER)
    response = client.get("/profiles/")
    assert response.status_code == 302
    assert response.location.endswith("/auth/confirm-email")


@pytest.mark.parametrize("role, status", [(ROLE_TRADESPERSON, VERIFICATION_VERIFIED), (ROLE_ADMIN, None)])
def test_other_roles_cannot_search(client, make_user, login, role, status):
    make_user(email="user@example.com", role=role, status=status, email_verified=True)
    login("user@example.com")
    assert client.get("/profiles/").status_code == 403


# --- 1) Search results --------------------------------------------------------------------


def test_search_lists_verified_tradespeople_with_badge(client, customer, make_tradesperson):
    make_tradesperson()
    page = search_page(client)
    assert "Ben Plumbing" in page
    assert "Plumber" in page
    assert "Verified" in page


@pytest.mark.parametrize("status", [VERIFICATION_PENDING, VERIFICATION_REJECTED, None])
@pytest.mark.parametrize("params", [{}, {"trade": "Plumber"}, {"city": "Berlin"}, {"trade": "Plumber", "city": "Berlin"}])
def test_unverified_tradespeople_never_appear(client, customer, make_tradesperson, status, params):
    make_tradesperson(company="Hidden GmbH", status=status)
    assert "Hidden GmbH" not in search_page(client, **params)


def test_search_filters_by_trade(client, customer, make_tradesperson):
    make_tradesperson(email=OWNER, company="Ben Plumbing", trades=("Plumber",))
    make_tradesperson(email=OTHER, company="Tina Tiles", trades=("Tiler", "Painter"))
    page = search_page(client, trade="Painter")
    assert "Tina Tiles" in page
    assert "Ben Plumbing" not in page


def test_search_filters_by_city(client, customer, make_tradesperson):
    make_tradesperson(email=OWNER, company="Ben Plumbing", area="Berlin")
    make_tradesperson(email=OTHER, company="Tina Tiles", area="Hamburg")
    page = search_page(client, city="Hamburg")
    assert "Tina Tiles" in page
    assert "Ben Plumbing" not in page


def test_search_combines_trade_and_city(client, customer, make_tradesperson):
    make_tradesperson(email=OWNER, company="Berlin Plumber", trades=("Plumber",), area="Berlin")
    make_tradesperson(email=OTHER, company="Hamburg Plumber", trades=("Plumber",), area="Hamburg")
    make_tradesperson(email="third@example.com", company="Berlin Tiler", trades=("Tiler",), area="Berlin")
    page = search_page(client, trade="Plumber", city="Berlin")
    assert "Berlin Plumber" in page
    assert "Hamburg Plumber" not in page
    assert "Berlin Tiler" not in page


@pytest.mark.parametrize("city", ["Potsdam", "potsdam", " BERLIN "])
def test_city_search_understands_several_cities_and_ignores_case(client, customer, make_tradesperson, city):
    make_tradesperson(area="Berlin, Potsdam")
    assert "Ben Plumbing" in search_page(client, city=city)


def test_city_search_needs_the_whole_city_name(client, customer, make_tradesperson):
    make_tradesperson(area="Berlin")
    assert "Ben Plumbing" not in search_page(client, city="Ber")


def test_unknown_trade_finds_nobody(client, customer, make_tradesperson):
    make_tradesperson()
    page = search_page(client, trade="Wizard")
    assert "Ben Plumbing" not in page
    assert "No verified tradespeople match" in page


def test_search_keeps_the_entered_filters(client, customer):
    page = search_page(client, trade="Plumber", city="Berlin")
    assert '<option value="Plumber" selected>' in page
    assert 'value="Berlin"' in page


def test_search_escapes_the_city(client, customer):
    assert "<script>alert(1)</script>" not in search_page(client, city="<script>alert(1)</script>")


# --- 2) Profile page ----------------------------------------------------------------------


def test_profile_shows_public_details_and_badge(client, make_tradesperson):
    profile_id = make_tradesperson(trades=("Plumber", "Tiler"), area="Berlin, Potsdam")
    page = client.get(f"/profiles/{profile_id}").get_data(as_text=True)
    for expected in ("Ben Plumbing", "Plumber, Tiler", "Berlin, Potsdam", "Fast and friendly.", "Verified"):
        assert expected in page


def test_profile_has_profile_sections(client, make_tradesperson):
    page = client.get(f"/profiles/{make_tradesperson()}").get_data(as_text=True)
    assert '<section id="availability"' in page
    assert '<section id="reviews"' in page
    assert "No upcoming availability." in page
    assert "No reviews yet." in page


def test_profile_shows_upcoming_availability_and_booking_link(app, client, customer, make_tradesperson):
    profile_id = make_tradesperson()
    with app.app_context():
        profile = db.session.get(TradespersonProfile, profile_id)
        profile.availability.append(AvailabilitySlot(weekday=(date.today().weekday() + 1) % 7, start_time=time(9), end_time=time(11)))
        job = Job(customer_id=db.session.scalar(db.select(User.id).where(User.email == CUSTOMER)), city="Munich", description="Fix it", urgency="normal")
        db.session.add(job)
        db.session.commit()
        job_id = job.id
    page = client.get(f"/profiles/{profile_id}?job_id={job_id}").get_data(as_text=True)
    assert "Book a slot" in page
    assert (date.today() + timedelta(days=1)).strftime("%d %b %Y") in page


def test_profile_shows_average_and_latest_reviews_with_first_name_only(app, client, make_tradesperson, make_user):
    profile_id = make_tradesperson()
    customer_id = make_user(email="reviewer@example.com", role=ROLE_CUSTOMER, email_verified=True)
    with app.app_context():
        profile = db.session.get(TradespersonProfile, profile_id)
        job = Job(customer_id=customer_id, title="Painting", city="Cologne", description="Walls", urgency="normal")
        db.session.add(job)
        db.session.flush()
        reviewer = db.session.get(User, customer_id)
        reviewer.name = "Alice Example"
        db.session.add(Review(job_id=job.id, tradesperson_id=profile.user_id, stars=4, comment="Great work"))
        db.session.commit()
    page = client.get(f"/profiles/{profile_id}").get_data(as_text=True)
    assert "Average rating: 4.0/5 (1 review)" in page
    assert "Alice" in page and "Example" not in page
    assert "Great work" in page


def test_date_format_filter_uses_shared_format(client):
    with client.application.app_context():
        rendered = client.application.jinja_env.filters["date_format"](date(2026, 11, 19))
    assert rendered == "19 Nov 2026"


def test_profile_without_description_still_loads(client, make_tradesperson):
    response = client.get(f"/profiles/{make_tradesperson(description=None)}")
    assert response.status_code == 200
    assert "No description yet" in response.get_data(as_text=True)


def test_profile_escapes_the_description(client, make_tradesperson):
    profile_id = make_tradesperson(description="<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in client.get(f"/profiles/{profile_id}").get_data(as_text=True)


@pytest.mark.parametrize("status", [VERIFICATION_PENDING, VERIFICATION_REJECTED, None])
def test_unverified_profile_is_not_found(client, make_tradesperson, status):
    assert client.get(f"/profiles/{make_tradesperson(status=status)}").status_code == 404


@pytest.mark.parametrize("status", [VERIFICATION_PENDING, VERIFICATION_REJECTED])
def test_unverified_owner_cannot_see_own_profile_either(client, make_tradesperson, login, status):
    profile_id = make_tradesperson(status=status)
    login(OWNER)
    assert client.get(f"/profiles/{profile_id}").status_code == 404


def test_unknown_profile_is_not_found(client):
    assert client.get("/profiles/999").status_code == 404


def test_edit_button_is_only_shown_to_the_owner(client, make_tradesperson, login):
    profile_id = make_tradesperson(email=OWNER)
    make_tradesperson(email=OTHER, company="Tina Tiles")
    url = f"/profiles/{profile_id}"

    assert "Edit my profile" not in client.get(url).get_data(as_text=True)
    login(OTHER)
    assert "Edit my profile" not in client.get(url).get_data(as_text=True)
    client.post("/auth/logout")
    login(OWNER)
    assert "Edit my profile" in client.get(url).get_data(as_text=True)


def test_my_profile_leads_to_own_profile(client, make_tradesperson, login):
    make_tradesperson(email=OTHER, company="Tina Tiles")
    profile_id = make_tradesperson(email=OWNER)
    login(OWNER)
    assert client.get("/profiles/me").location.endswith(f"/profiles/{profile_id}")


def test_my_profile_is_only_for_verified_tradespeople(client, make_tradesperson, login):
    assert_sent_to_login(client.get("/profiles/me"))
    make_tradesperson(email=OWNER, status=VERIFICATION_PENDING)
    login(OWNER)
    assert client.get("/profiles/me").status_code == 403


# --- 2) Private data never appears --------------------------------------------------------


def assert_no_private_data(page):
    for value in PRIVATE_VALUES.values():
        assert value not in page
    assert OWNER not in page


def test_search_results_hide_private_data(client, customer, make_tradesperson):
    make_tradesperson()
    page = search_page(client)
    assert "Ben Plumbing" in page
    assert_no_private_data(page)


def test_profile_hides_private_data_from_visitors(client, make_tradesperson):
    response = client.get(f"/profiles/{make_tradesperson()}")
    assert response.status_code == 200
    assert_no_private_data(response.get_data(as_text=True))


def test_profile_hides_private_data_from_customers(client, customer, make_tradesperson):
    response = client.get(f"/profiles/{make_tradesperson()}")
    assert response.status_code == 200
    assert_no_private_data(response.get_data(as_text=True))


def test_searching_for_private_data_finds_nothing(client, customer, make_tradesperson):
    make_tradesperson(area="Berlin")
    assert "Ben Plumbing" not in search_page(client, city="Hamburg")
    assert "Ben Plumbing" not in search_page(client, city=PRIVATE_VALUES["address"])


# --- 3) Who can edit ----------------------------------------------------------------------


@pytest.mark.parametrize("method", ["get", "post"])
def test_anonymous_visitor_cannot_edit(app, client, make_tradesperson, method):
    profile_id = make_tradesperson()
    before = saved_profile(app, profile_id)
    assert_sent_to_login(getattr(client, method)(f"/profiles/{profile_id}/edit", data=edit_form()))
    assert saved_profile(app, profile_id) == before


@pytest.mark.parametrize("method", ["get", "post"])
def test_tradesperson_cannot_edit_somebody_elses_profile(app, client, make_tradesperson, login, method):
    victim = make_tradesperson(email=OWNER)
    make_tradesperson(email=OTHER, company="Tina Tiles")
    before = saved_profile(app, victim)
    login(OTHER)
    assert getattr(client, method)(f"/profiles/{victim}/edit", data=edit_form()).status_code == 403
    assert saved_profile(app, victim) == before


@pytest.mark.parametrize(
    "email, role, status",
    [(CUSTOMER, ROLE_CUSTOMER, None), ("admin@example.com", ROLE_ADMIN, None)],
)
@pytest.mark.parametrize("method", ["get", "post"])
def test_other_roles_cannot_edit(app, client, make_tradesperson, make_user, login, email, role, status, method):
    profile_id = make_tradesperson()
    before = saved_profile(app, profile_id)
    make_user(email=email, role=role, status=status, email_verified=True)
    login(email)
    assert getattr(client, method)(f"/profiles/{profile_id}/edit", data=edit_form()).status_code == 403
    assert saved_profile(app, profile_id) == before


@pytest.mark.parametrize("status", [VERIFICATION_PENDING, VERIFICATION_REJECTED])
@pytest.mark.parametrize("method", ["get", "post"])
def test_unverified_owner_cannot_edit(app, client, make_tradesperson, login, status, method):
    profile_id = make_tradesperson(status=status)
    before = saved_profile(app, profile_id)
    login(OWNER)
    assert getattr(client, method)(f"/profiles/{profile_id}/edit", data=edit_form()).status_code == 403
    assert saved_profile(app, profile_id) == before


def test_edit_of_unknown_profile_is_not_found(client, make_tradesperson, login):
    make_tradesperson()
    login(OWNER)
    assert client.get("/profiles/999/edit").status_code == 404


def test_edit_form_checks_csrf_token():
    class CsrfOn(TestConfig):
        WTF_CSRF_ENABLED = True

    app = create_app(CsrfOn)
    with app.app_context():
        user = User(
            email=OWNER,
            name="Ben",
            role=ROLE_TRADESPERSON,
            verification_status=VERIFICATION_VERIFIED,
            email_verified=True,
        )
        user.set_password(PASSWORD)
        user.accept_terms()
        user.profile = TradespersonProfile(company_name="Ben Plumbing", service_area="Berlin")
        db.session.add(user)
        db.session.commit()
        user_id, profile_id = user.id, user.profile.id

    client = app.test_client()
    # Logging in also needs a token here, so put the login straight into the session.
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True

    assert client.post(f"/profiles/{profile_id}/edit", data=edit_form()).status_code == 400
    assert saved_profile(app, profile_id)["service_area"] == "Berlin"


# --- 3) Editing own profile ---------------------------------------------------------------


@pytest.fixture
def owner_profile(make_tradesperson, login):
    profile_id = make_tradesperson(trades=("Plumber", "Tiler"), area="Berlin")
    login(OWNER)
    return profile_id


def test_edit_form_shows_saved_values(client, owner_profile):
    page = client.get(f"/profiles/{owner_profile}/edit").get_data(as_text=True)
    assert "Fast and friendly." in page
    assert 'value="Berlin"' in page
    assert 'value="Plumber" checked' in page
    assert 'value="Tiler" checked' in page
    assert 'value="Roofer" checked' not in page


def test_edit_form_hides_private_data(client, owner_profile):
    # Their own name is in the navbar, which is fine; phone and address have no place on this form.
    page = client.get(f"/profiles/{owner_profile}/edit").get_data(as_text=True)
    assert PRIVATE_VALUES["phone"] not in page
    assert PRIVATE_VALUES["address"] not in page


def test_owner_can_edit_description_trades_and_area(app, client, owner_profile):
    response = client.post(
        f"/profiles/{owner_profile}/edit",
        data=edit_form(description="Now with tiles", service_area="Potsdam, Berlin", trades=["Painter", "Roofer"]),
    )
    assert response.location.endswith(f"/profiles/{owner_profile}")
    saved = saved_profile(app, owner_profile)
    assert saved["description"] == "Now with tiles"
    assert saved["service_area"] == "Potsdam, Berlin"
    assert saved["trades"] == ["Painter", "Roofer"]


def test_owner_can_keep_a_trade_while_adding_another(app, client, owner_profile):
    # Keeping a row must not break the unique constraint on profile and trade.
    client.post(f"/profiles/{owner_profile}/edit", data=edit_form(trades=["Plumber", "Painter"]))
    assert saved_profile(app, owner_profile)["trades"] == ["Painter", "Plumber"]


def test_saving_without_changes_keeps_the_trades(app, client, owner_profile):
    client.post(f"/profiles/{owner_profile}/edit", data=edit_form(trades=["Plumber", "Tiler"]))
    assert saved_profile(app, owner_profile)["trades"] == ["Plumber", "Tiler"]


def test_removed_trade_no_longer_finds_the_profile(client, owner_profile, make_user, login):
    client.post(f"/profiles/{owner_profile}/edit", data=edit_form(service_area="Berlin", trades=["Plumber"]))
    client.post("/auth/logout")
    make_user(email=CUSTOMER, role=ROLE_CUSTOMER, email_verified=True)
    login(CUSTOMER)
    assert "Ben Plumbing" not in search_page(client, trade="Tiler")
    assert "Ben Plumbing" in search_page(client, trade="Plumber")


def test_description_can_be_emptied(app, client, owner_profile):
    client.post(f"/profiles/{owner_profile}/edit", data=edit_form(description=""))
    assert saved_profile(app, owner_profile)["description"] == ""


@pytest.mark.parametrize(
    "changes, field, message",
    [
        ({"trades": []}, "trades", "at least one trade"),
        ({"trades": ["Wizard"]}, "trades", "at least one trade"),
        ({"service_area": ""}, "service_area", "city or service area"),
        ({"service_area": "   "}, "service_area", "city or service area"),
        ({"service_area": " , "}, "service_area", "city or service area"),
        ({"service_area": "x" * 121}, "service_area", "at most 120"),
        ({"description": "x" * 1001}, "description", "at most 1000"),
    ],
)
def test_invalid_edit_is_refused_with_a_message(app, client, owner_profile, changes, field, message):
    before = saved_profile(app, owner_profile)
    response = client.post(f"/profiles/{owner_profile}/edit", data=edit_form(**changes))
    assert response.status_code == 200
    assert message in response.get_data(as_text=True)
    assert saved_profile(app, owner_profile) == before


def test_invented_trade_is_dropped_when_a_real_one_is_chosen(app, client, owner_profile):
    client.post(f"/profiles/{owner_profile}/edit", data=edit_form(trades=["Plumber", "Wizard"]))
    assert saved_profile(app, owner_profile)["trades"] == ["Plumber"]


def test_failed_edit_keeps_what_was_typed(client, owner_profile):
    response = client.post(
        f"/profiles/{owner_profile}/edit", data=edit_form(description="Typed text", service_area="", trades=["Roofer"])
    )
    page = response.get_data(as_text=True)
    assert "Typed text" in page
    assert 'value="Roofer" checked' in page
    assert 'value="Plumber" checked' not in page


def test_edit_cannot_change_anything_else(app, client, owner_profile):
    # A hand-made request can send extra fields; the route must ignore them.
    before = saved_profile(app, owner_profile)
    client.post(
        f"/profiles/{owner_profile}/edit",
        data=edit_form(
            company_name="Fake Name",
            qualification_reference="FAKE",
            verification_status=VERIFICATION_REJECTED,
            role=ROLE_ADMIN,
            phone="000000",
            user_id="1",
        ),
    )
    saved = saved_profile(app, owner_profile)
    assert saved["company_name"] == before["company_name"]
    assert saved["qualification_reference"] == before["qualification_reference"]
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(email=OWNER))
        assert user.verification_status == VERIFICATION_VERIFIED
        assert user.role == ROLE_TRADESPERSON
        assert user.phone == PRIVATE_VALUES["phone"]
        assert user.profile.id == owner_profile
