from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager

ROLE_CUSTOMER = "customer"
ROLE_TRADESPERSON = "tradesperson"
ROLE_ADMIN = "admin"

VERIFICATION_PENDING = "pending"
VERIFICATION_VERIFIED = "verified"
VERIFICATION_REJECTED = "rejected"

JOB_OPEN = "open"
JOB_IN_PROGRESS = "in_progress"
JOB_DONE = "done"

QUOTE_PENDING = "pending"
QUOTE_ACCEPTED = "accepted"
QUOTE_REJECTED = "rejected"

# A fixed list, because free text ("Plumber", "plumbing") could never be matched to jobs.
TRADES = ("Carpenter", "Electrician", "Painter", "Plumber", "Roofer", "Tiler")


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    # Only tradespeople have a status. Empty never counts as verified, so the safe case is the default.
    verification_status = db.Column(db.String(20))
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    # Private: stay hidden from a tradesperson until a quote is accepted or a slot is booked.
    phone = db.Column(db.String(30))
    address = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    profile = db.relationship(
        "TradespersonProfile", backref="user", uselist=False, cascade="all, delete-orphan"
    )
    jobs = db.relationship("Job", backref="customer")
    quotes = db.relationship("Quote", backref="tradesperson")
    bookings = db.relationship("Booking", backref="tradesperson")
    reviews_received = db.relationship("Review", backref="tradesperson")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_verified_tradesperson(self):
        return self.role == ROLE_TRADESPERSON and self.verification_status == VERIFICATION_VERIFIED


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def normalise_email(email):
    # So Anna@example.com and anna@example.com cannot become two accounts.
    return email.strip().lower()


def find_user_by_email(email):
    return db.session.scalar(db.select(User).filter_by(email=normalise_email(email)))


def ensure_admin(email, password):
    # Runs at every startup: the free Render plan has no shell and loses its database on restart.
    if not email or not password or find_user_by_email(email):
        return
    admin = User(email=normalise_email(email), name="Admin", role=ROLE_ADMIN, email_verified=True)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()


class TradespersonProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), unique=True, nullable=False)
    company_name = db.Column(db.String(120), nullable=False)
    # A reference number only: we deliberately store no uploaded documents (GDPR).
    qualification_reference = db.Column(db.String(50))
    service_area = db.Column(db.String(120))

    trades = db.relationship("TradespersonTrade", backref="profile", cascade="all, delete-orphan")
    availability = db.relationship("AvailabilitySlot", backref="profile", cascade="all, delete-orphan")


class TradespersonTrade(db.Model):
    # Its own table, not a text list, so "who offers plumbing?" is a simple query.
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.ForeignKey("tradesperson_profile.id"), nullable=False)
    trade = db.Column(db.String(50), nullable=False)

    __table_args__ = (db.UniqueConstraint("profile_id", "trade"),)


class AvailabilitySlot(db.Model):
    # A repeating weekly window. A concrete appointment is a Booking.
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.ForeignKey("tradesperson_profile.id"), nullable=False)
    weekday = db.Column(db.Integer, nullable=False)  # 0 = Monday, matches date.weekday()
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    city = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    urgency = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=JOB_OPEN)
    # Small jobs can be booked directly into a free slot instead of collecting quotes.
    is_small = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    trades = db.relationship("JobTrade", backref="job", cascade="all, delete-orphan")
    bookings = db.relationship("Booking", backref="job", cascade="all, delete-orphan")
    reviews = db.relationship("Review", backref="job", cascade="all, delete-orphan")
    project = db.relationship("Project", backref="job", uselist=False, cascade="all, delete-orphan")


class JobTrade(db.Model):
    # Every job has one row per trade it needs, so single- and multi-trade jobs work the same way.
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.ForeignKey("job.id"), nullable=False)
    trade = db.Column(db.String(50), nullable=False)

    quotes = db.relationship("Quote", backref="job_trade", cascade="all, delete-orphan")

    __table_args__ = (db.UniqueConstraint("job_id", "trade"),)


class Quote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Linked to the trade, not the job: on a multi-trade job each trade gets its own quotes.
    job_trade_id = db.Column(db.ForeignKey("job_trade.id"), nullable=False)
    tradesperson_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    # Whole cents, because floats cannot store money exactly.
    price_cents = db.Column(db.Integer, nullable=False)
    message = db.Column(db.Text)
    earliest_start = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=QUOTE_PENDING)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    __table_args__ = (db.UniqueConstraint("job_trade_id", "tradesperson_id"),)


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.ForeignKey("job.id"), nullable=False)
    tradesperson_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    starts_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    # The database itself blocks two customers from booking the same slot.
    __table_args__ = (db.UniqueConstraint("tradesperson_id", "starts_at"),)


class Project(db.Model):
    # Members are not stored: they are the tradespeople with an accepted quote on this job.
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.ForeignKey("job.id"), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    comments = db.relationship("ProjectComment", backref="project", cascade="all, delete-orphan")
    schedules = db.relationship("ProjectSchedule", backref="project", cascade="all, delete-orphan")


class ProjectSchedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.ForeignKey("project.id"), nullable=False)
    job_trade_id = db.Column(db.ForeignKey("job_trade.id"), nullable=False)
    tradesperson_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)

    tradesperson = db.relationship("User")
    job_trade = db.relationship("JobTrade")

    __table_args__ = (db.UniqueConstraint("project_id", "job_trade_id"),)


class ProjectComment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.ForeignKey("project.id"), nullable=False)
    author_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    # Filled in when the comment proposes a date, which puts it on the customer's timeline.
    proposed_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    author = db.relationship("User")


class Review(db.Model):
    # No customer column: the reviewer is always job.customer, so the two can never disagree.
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.ForeignKey("job.id"), nullable=False)
    tradesperson_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    stars = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("job_id", "tradesperson_id"),  # one review only
        db.CheckConstraint("stars BETWEEN 1 AND 5"),
    )
