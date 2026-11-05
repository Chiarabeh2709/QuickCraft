# Feature 7: invented demo data. Runs only when SEED_DEMO_DATA=true, see DEMO.md.
from datetime import time, timedelta

from flask import current_app
from werkzeug.security import generate_password_hash

from app.blueprints.bookings import now_local, upcoming_starts
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
    TRADE_PENDING,
    VERIFICATION_PENDING,
    VERIFICATION_VERIFIED,
    AvailabilitySlot,
    Booking,
    Job,
    JobTrade,
    Project,
    ProjectComment,
    ProjectSchedule,
    Quote,
    Review,
    TradespersonProfile,
    TradespersonTrade,
    User,
)

# Public on purpose: these accounts are fake, and DEMO.md lists them. The admin never uses this password.
DEMO_PASSWORD = "demo-password-123"

MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY = range(5)
MORNING = (time(9), time(12))
AFTERNOON = (time(13), time(16))

# The phone numbers come from the blocks that the German network agency reserves for fictional use.
CUSTOMERS = {
    "emma": ("Emma Carter", "emma.carter@example.com", "+49 30 23125 001", "14 Maple Street, 10115 Berlin"),
    "james": ("James Walker", "james.walker@example.com", "+49 40 66969 001", "8 Harbor View, 20095 Hamburg"),
    "olivia": ("Olivia Bennett", "olivia.bennett@example.com", "+49 89 99998 001", "27 Oak Avenue, 80331 Munich"),
    "thomas": ("Thomas Reed", "thomas.reed@example.com", "+49 221 4710 001", "3 River Lane, 50667 Cologne"),
}

TRADESPEOPLE = {
    "harbor": {
        "name": "Daniel Hughes",
        "email": "daniel.hughes@example.com",
        "phone": "+49 30 23125 002",
        "address": "5 Dock Road, 10117 Berlin",
        "company": "Harbor Plumbing Ltd",
        "reference": "DEMO-REF-1001",
        "area": "Berlin, Hamburg",
        "description": "Family plumbing business. Leaks, taps, showers and pipework, usually within a few days.",
        "trades": {"Plumber": TRADE_APPROVED},
        "slots": ((MONDAY, MORNING), (WEDNESDAY, MORNING), (FRIDAY, AFTERNOON)),
    },
    "spark": {
        "name": "Sarah Mitchell",
        "email": "sarah.mitchell@example.com",
        "phone": "+49 40 66969 002",
        "address": "19 Copper Close, 20097 Hamburg",
        "company": "Bright Spark Electrics",
        "reference": "DEMO-REF-1002",
        "area": "Berlin, Hamburg",
        "description": "Qualified electrician for sockets, lighting and fuse boxes. Tidy work and clear prices.",
        "trades": {"Electrician": TRADE_APPROVED},
        "slots": ((TUESDAY, MORNING), (THURSDAY, AFTERNOON)),
    },
    "coat": {
        "name": "Michael Turner",
        "email": "michael.turner@example.com",
        "phone": "+49 221 4710 002",
        "address": "41 Linden Road, 50668 Cologne",
        "company": "Fresh Coat Painting",
        "reference": "DEMO-REF-1003",
        "area": "Berlin, Munich, Cologne",
        "description": "Interior painting for flats and houses. Furniture is covered and the room left clean.",
        "trades": {"Painter": TRADE_APPROVED},
        "slots": ((MONDAY, AFTERNOON), (THURSDAY, MORNING)),
    },
    "tiling": {
        "name": "Laura Collins",
        "email": "laura.collins@example.com",
        "phone": "+49 89 99998 002",
        "address": "6 Stone Yard, 80333 Munich",
        "company": "Straight Line Tiling",
        "reference": "DEMO-REF-1004",
        "area": "Berlin, Munich",
        "description": "Wall and floor tiling for bathrooms and kitchens, with careful preparation of every surface.",
        "trades": {"Tiler": TRADE_APPROVED},
        "slots": ((TUESDAY, AFTERNOON), (FRIDAY, MORNING)),
    },
    "oak": {
        "name": "Peter Morgan",
        "email": "peter.morgan@example.com",
        "phone": "+49 89 99998 003",
        "address": "22 Timber Way, 80335 Munich",
        "company": "Solid Oak Carpentry",
        "reference": "DEMO-REF-1005",
        "area": "Berlin, Munich, Cologne",
        "description": "Doors, shelves and fitted furniture, made to measure in our own workshop.",
        "trades": {"Carpenter": TRADE_APPROVED},
        "slots": ((WEDNESDAY, AFTERNOON), (FRIDAY, MORNING)),
    },
    "warm": {
        "name": "Rachel Foster",
        "email": "rachel.foster@example.com",
        "phone": "+49 40 66969 003",
        "address": "11 Boiler Row, 20099 Hamburg",
        "company": "Warm Home Heating",
        "reference": "DEMO-REF-1006",
        "area": "Hamburg, Cologne",
        "description": "Boiler servicing, radiators and heating repairs, with a quick response in winter.",
        "trades": {"Heating engineer": TRADE_APPROVED},
        "slots": ((MONDAY, MORNING), (WEDNESDAY, AFTERNOON)),
    },
    "allround": {
        "name": "Andrew Price",
        "email": "andrew.price@example.com",
        "phone": "+49 221 4710 003",
        "address": "9 Mill Street, 50670 Cologne",
        "company": "All Round Home Services",
        "reference": "DEMO-REF-1007",
        "area": "Berlin, Munich, Cologne",
        "description": "One team for plumbing and heating, from a dripping tap to a new radiator.",
        "trades": {"Plumber": TRADE_APPROVED, "Heating engineer": TRADE_APPROVED},
        "slots": ((TUESDAY, MORNING), (THURSDAY, MORNING), (FRIDAY, AFTERNOON)),
    },
    # One approved and one pending trade, so the "Pending trades" list on the admin page is not empty.
    "roofing": {
        "name": "Helen Ward",
        "email": "helen.ward@example.com",
        "phone": "+49 30 23125 003",
        "address": "30 Slate Street, 10119 Berlin",
        "company": "Top Floor Roofing",
        "reference": "DEMO-REF-1008",
        "area": "Berlin, Hamburg",
        "description": "Roof repairs and gutters. Now also offering carpentry for roof windows.",
        "trades": {"Roofer": TRADE_APPROVED, "Carpenter": TRADE_PENDING},
        "slots": ((MONDAY, AFTERNOON), (WEDNESDAY, MORNING)),
    },
    # Account not approved yet, so the "Pending tradespeople" list on the admin page is not empty.
    "newstart": {
        "name": "Mark Evans",
        "email": "mark.evans@example.com",
        "phone": "+49 30 23125 004",
        "address": "2 Birch Court, 10178 Berlin",
        "company": "New Start Decorating",
        "reference": "DEMO-REF-1009",
        "area": "Berlin",
        "description": "Decorating for small flats and offices.",
        "trades": {"Painter": TRADE_PENDING},
        "status": VERIFICATION_PENDING,
        "slots": (),
    },
}

# The order sets the job ids that DEMO.md links to, so add new jobs at the end.
JOBS = {
    "leak": {
        "customer": "emma",
        "title": "Burst pipe under the kitchen sink",
        "city": "Berlin",
        "description": "Water is leaking from the pipe under the sink. I have closed the valve but need it fixed soon.",
        "trades": ("Plumber",),
        "urgency": "urgent",
    },
    "roof": {
        "customer": "james",
        "title": "Roof leak after the storm",
        "city": "Hamburg",
        "description": "Water is coming through the bedroom ceiling. A few roof tiles seem to have moved.",
        "trades": ("Roofer",),
        "urgency": "urgent",
    },
    "bathroom": {
        "customer": "emma",
        "title": "Full bathroom renovation",
        "city": "Berlin",
        "description": "New shower, new wall tiles, two extra sockets and fresh paint for our small bathroom.",
        "trades": ("Plumber", "Tiler", "Electrician", "Painter"),
    },
    "boiler": {
        "customer": "thomas",
        "title": "Service the boiler before winter",
        "city": "Cologne",
        "description": "Yearly service for a ten year old boiler. One radiator also stays cold.",
        "trades": ("Heating engineer",),
    },
    "tap": {
        "customer": "olivia",
        "title": "Fix a dripping bathroom tap",
        "city": "Munich",
        "description": "The cold water tap keeps dripping. It should be a quick repair.",
        "trades": ("Plumber",),
        "is_small": True,
    },
    "shelves": {
        "customer": "emma",
        "title": "Build fitted shelves in the study",
        "city": "Berlin",
        "description": "Floor to ceiling shelves along one wall, about three metres wide.",
        "trades": ("Carpenter",),
    },
    "shower": {
        "customer": "emma",
        "title": "Replace the old shower",
        "city": "Berlin",
        "description": "The shower head and mixer are worn out and need replacing.",
        "trades": ("Plumber",),
        "status": JOB_IN_PROGRESS,
    },
    "door": {
        "customer": "thomas",
        "title": "Repair a sticking door",
        "city": "Cologne",
        "description": "The living room door scrapes along the floor and no longer closes properly.",
        "trades": ("Carpenter",),
        "is_small": True,
        "status": JOB_IN_PROGRESS,
    },
    "switches": {
        "customer": "james",
        "title": "Replace two light switches",
        "city": "Hamburg",
        "description": "Two old switches in the hallway should be replaced.",
        "trades": ("Electrician",),
        "is_small": True,
        "status": JOB_IN_PROGRESS,
    },
    "kitchen": {
        "customer": "olivia",
        "title": "Kitchen makeover",
        "city": "Munich",
        "description": "New wall tiles behind the worktop and new fitted cabinets, in that order.",
        "trades": ("Tiler", "Carpenter"),
        "status": JOB_IN_PROGRESS,
    },
    "bedroom": {
        "customer": "thomas",
        "title": "Repaint the bedroom",
        "city": "Cologne",
        "description": "Walls and ceiling in white, about twenty square metres.",
        "trades": ("Painter",),
        "status": JOB_DONE,
    },
    "gate": {
        "customer": "james",
        "title": "Build a new garden gate",
        "city": "Hamburg",
        "description": "A simple wooden gate. No longer needed because we are moving.",
        "trades": ("Carpenter",),
        "status": JOB_CANCELLED,
    },
}

# Job, trade, tradesperson, price in cents, earliest start in days from today, status, message.
QUOTES = (
    ("boiler", "Heating engineer", "warm", 18000, 5, QUOTE_PENDING, "Service and radiator check, parts included."),
    ("boiler", "Heating engineer", "allround", 16500, 9, QUOTE_PENDING, "A little later but cheaper, same service."),
    ("bathroom", "Plumber", "harbor", 145000, 14, QUOTE_PENDING, "New shower and all pipework for the bathroom."),
    ("shower", "Plumber", "harbor", 32000, 7, QUOTE_ACCEPTED, "Includes a new mixer and removing the old one."),
    ("shower", "Plumber", "allround", 35000, 12, QUOTE_REJECTED, "Includes a new mixer."),
    ("kitchen", "Tiler", "tiling", 98000, 14, QUOTE_ACCEPTED, "Wall tiles behind the worktop, about six square metres."),
    ("kitchen", "Carpenter", "oak", 240000, 17, QUOTE_ACCEPTED, "Cabinets made to measure, fitted over four days."),
    ("bedroom", "Painter", "coat", 42000, -21, QUOTE_ACCEPTED, "Two coats for walls and ceiling, paint included."),
)


def create_customers(password_hash):
    customers = {}
    for key, (name, email, phone, address) in CUSTOMERS.items():
        customers[key] = User(
            name=name,
            email=email,
            phone=phone,
            address=address,
            role=ROLE_CUSTOMER,
            email_verified=True,
            password_hash=password_hash,
        )
    db.session.add_all(customers.values())
    return customers


def create_profile(data):
    return TradespersonProfile(
        company_name=data["company"],
        qualification_reference=data["reference"],
        service_area=data["area"],
        description=data["description"],
        trades=[TradespersonTrade(trade=trade, status=status) for trade, status in data["trades"].items()],
        availability=[
            AvailabilitySlot(weekday=weekday, start_time=start, end_time=end)
            for weekday, (start, end) in data["slots"]
        ],
    )


def create_tradespeople(password_hash):
    people = {}
    for key, data in TRADESPEOPLE.items():
        people[key] = User(
            name=data["name"],
            email=data["email"],
            phone=data["phone"],
            address=data["address"],
            role=ROLE_TRADESPERSON,
            verification_status=data.get("status", VERIFICATION_VERIFIED),
            email_verified=True,
            password_hash=password_hash,
            profile=create_profile(data),
        )
    db.session.add_all(people.values())
    return people


def create_jobs(customers):
    jobs = {}
    for key, data in JOBS.items():
        jobs[key] = Job(
            customer=customers[data["customer"]],
            title=data["title"],
            city=data["city"],
            description=data["description"],
            urgency=data.get("urgency", "normal"),
            status=data.get("status", JOB_OPEN),
            is_small=data.get("is_small", False),
            trades=[JobTrade(trade=trade) for trade in data["trades"]],
        )
        db.session.add(jobs[key])
        # One flush per job, so the ids follow the order of JOBS and the links in DEMO.md stay right.
        db.session.flush()
    return jobs


def job_trade(job, trade):
    return next(entry for entry in job.trades if entry.trade == trade)


def create_quotes(jobs, people, today):
    for job_key, trade, person, price_cents, days, status, message in QUOTES:
        db.session.add(
            Quote(
                job_trade=job_trade(jobs[job_key], trade),
                tradesperson=people[person],
                price_cents=price_cents,
                earliest_start=today + timedelta(days=days),
                status=status,
                message=message,
            )
        )


def create_booking(job, person, slot_index, week):
    # A real start of one of their weekly slots, so it looks exactly like a booking made in the app.
    slot = person.profile.availability[slot_index]
    db.session.add(Booking(job=job, tradesperson=person, starts_at=upcoming_starts(slot, now_local())[week]))


def create_bookings(jobs, people):
    create_booking(jobs["shower"], people["harbor"], 0, 1)  # The appointment on the accepted job
    create_booking(jobs["door"], people["oak"], 0, 1)
    create_booking(jobs["switches"], people["spark"], 0, 2)


def create_project(jobs, people, customers, today):
    job = jobs["kitchen"]
    project = Project(job=job)
    tiles_start = today + timedelta(days=14)
    cabinets_start = today + timedelta(days=17)
    for trade, person, start, end in (
        ("Tiler", people["tiling"], tiles_start, tiles_start + timedelta(days=2)),
        ("Carpenter", people["oak"], cabinets_start, cabinets_start + timedelta(days=3)),
    ):
        project.schedules.append(
            ProjectSchedule(job_trade=job_trade(job, trade), tradesperson=person, start_date=start, end_date=end)
        )
    # The dates are in the text too, because the project page does not show proposed_date yet.
    for author, body, proposed_date in (
        (customers["olivia"], "Thanks both. The tiles need to be finished before the cabinets go in.", None),
        (people["tiling"], f"I can start on {tiles_start:%d %b} and need three days.", tiles_start),
        (people["oak"], f"Then I will start fitting on {cabinets_start:%d %b}.", cabinets_start),
        (customers["olivia"], "That works for me. Please park in the yard behind the house.", None),
    ):
        project.comments.append(ProjectComment(author=author, body=body, proposed_date=proposed_date))
    db.session.add(project)


def create_review(jobs, people):
    db.session.add(
        Review(
            job=jobs["bedroom"],
            tradesperson=people["coat"],
            stars=5,
            comment="Careful, clean work and finished a day early. I would happily book again.",
        )
    )


def create_demo_data():
    # Hashed once: hashing is the slow part, and every demo account has the same password anyway.
    password_hash = generate_password_hash(DEMO_PASSWORD)
    today = now_local().date()
    customers = create_customers(password_hash)
    people = create_tradespeople(password_hash)
    jobs = create_jobs(customers)
    create_quotes(jobs, people, today)
    create_bookings(jobs, people)
    create_project(jobs, people, customers, today)
    create_review(jobs, people)


def has_users_besides_admin():
    return db.session.scalar(db.select(User.id).where(User.role != ROLE_ADMIN).limit(1)) is not None


def seed_demo_data():
    """Returns True if it created the data. Once anyone else has an account, it never touches the database."""
    try:
        if has_users_besides_admin():
            return False
        create_demo_data()
        # One commit for everything, so a failure can never leave half of the demo data behind.
        db.session.commit()
        return True
    except Exception as error:
        # Broad on purpose: demo data must never stop the real app from starting.
        db.session.rollback()
        current_app.logger.warning("Demo data was not created: %s", error)
        return False


def seed_if_enabled():
    return current_app.config["SEED_DEMO_DATA"] and seed_demo_data()
