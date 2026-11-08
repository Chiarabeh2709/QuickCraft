from datetime import date, time, timedelta

from app.extensions import db
from app.models import AvailabilitySlot, Job, Review, ROLE_TRADESPERSON, TradespersonProfile, User


def test_profile_shows_upcoming_availability_and_latest_review(client, app, make_user):
    tradesperson = make_user(
        email="profile-worker@example.com",
        role=ROLE_TRADESPERSON,
        status="verified",
    )
    customer = make_user(email="profile-customer@example.com", email_verified=True)
    with app.app_context():
        db.session.get(User, customer).name = "Anna Customer"
        profile = TradespersonProfile(user_id=tradesperson, company_name="Bright Repairs")
        db.session.add(profile)
        tomorrow = date.today() + timedelta(days=1)
        db.session.add(AvailabilitySlot(profile=profile, weekday=tomorrow.weekday(), start_time=time(9), end_time=time(10)))
        job = Job(customer_id=customer, city="Munich", description="Repair a door", urgency="normal")
        db.session.add(job)
        db.session.flush()
        db.session.add(Review(job_id=job.id, tradesperson_id=tradesperson, stars=5, comment="Excellent work"))
        db.session.commit()

    response = client.get(f"/profiles/{tradesperson}")
    assert response.status_code == 200
    assert b"Bright Repairs" in response.data
    assert b"Excellent work" in response.data
    assert b"Anna" in response.data
    assert b"Book a slot" not in response.data


def test_profile_links_availability_to_booking_flow_for_a_job(client, app, make_user):
    tradesperson = make_user(email="book-profile@example.com", role=ROLE_TRADESPERSON, status="verified")
    customer = make_user(email="book-customer@example.com", email_verified=True)
    with app.app_context():
        profile = TradespersonProfile(user_id=tradesperson, company_name="Bookable Repairs")
        db.session.add(profile)
        tomorrow = date.today() + timedelta(days=1)
        db.session.add(AvailabilitySlot(profile=profile, weekday=tomorrow.weekday(), start_time=time(9), end_time=time(10)))
        job = Job(customer_id=customer, city="Cologne", description="Paint a room", urgency="normal", is_small=True)
        db.session.add(job)
        db.session.commit()
        job_id = job.id

    response = client.get(f"/profiles/{tradesperson}?job_id={job_id}")
    assert response.status_code == 200
    assert f"/bookings/job/{job_id}".encode() in response.data


def test_shared_date_filter_formats_dates(app):
    with app.app_context():
        rendered = app.jinja_env.from_string("{{ value|date_format }}").render(value=date(2026, 11, 19))
    assert rendered == "19 Nov 2026"
