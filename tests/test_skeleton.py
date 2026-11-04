import pytest
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import ROLE_CUSTOMER, ROLE_TRADESPERSON, VERIFICATION_PENDING, VERIFICATION_VERIFIED, Job, Review, User
from config import TestConfig


def make_user(role=ROLE_CUSTOMER, email="a@example.com", **fields):
    user = User(email=email, name="Test", role=role, **fields)
    user.set_password("secret")
    db.session.add(user)
    db.session.commit()
    return user


def make_job(customer):
    job = Job(customer_id=customer.id, city="Berlin", description="Fix a tap", urgency="normal")
    db.session.add(job)
    db.session.commit()
    return job


def test_home_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"QuickCraft" in response.data


def test_app_refuses_to_start_without_secret_key():
    class NoKey(TestConfig):
        SECRET_KEY = None

    with pytest.raises(RuntimeError):
        create_app(NoKey)


def test_password_is_stored_hashed(app):
    user = make_user()
    assert user.password_hash != "secret"
    assert user.check_password("secret")
    assert not user.check_password("wrong")


def test_new_user_has_unverified_email(app):
    assert make_user().email_verified is False


@pytest.mark.parametrize(
    "role, status, expected",
    [
        (ROLE_TRADESPERSON, VERIFICATION_VERIFIED, True),
        (ROLE_TRADESPERSON, VERIFICATION_PENDING, False),
        (ROLE_TRADESPERSON, None, False),
        (ROLE_CUSTOMER, VERIFICATION_VERIFIED, False),
    ],
)
def test_is_verified_tradesperson(app, role, status, expected):
    user = make_user(role=role, verification_status=status)
    assert user.is_verified_tradesperson is expected


def test_database_rejects_unknown_foreign_key(app):
    # Proves the SQLite foreign key switch in extensions.py is really on.
    db.session.add(Job(customer_id=999, city="Berlin", description="x", urgency="normal"))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_database_rejects_stars_out_of_range(app):
    customer = make_user()
    tradesperson = make_user(role=ROLE_TRADESPERSON, email="b@example.com")
    job = make_job(customer)
    db.session.add(Review(job_id=job.id, tradesperson_id=tradesperson.id, stars=6))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_database_rejects_second_review(app):
    customer = make_user()
    tradesperson = make_user(role=ROLE_TRADESPERSON, email="b@example.com")
    job = make_job(customer)
    db.session.add(Review(job_id=job.id, tradesperson_id=tradesperson.id, stars=5))
    db.session.commit()
    db.session.add(Review(job_id=job.id, tradesperson_id=tradesperson.id, stars=1))
    with pytest.raises(IntegrityError):
        db.session.commit()
