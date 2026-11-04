# QuickCraft

A web marketplace for tradespeople (Handwerker) in the style of MyHammer, built Doctolib-like: clean, fast, few steps.

This is a course project. It must be deployed at a public URL, and every team member must be able to explain all of the code. Prefer simple, readable code over clever code.

## Stack

- Python, Flask, Jinja templates, Bootstrap
- SQLite via SQLAlchemy
- Flask-Login for authentication, Flask-WTF only for CSRF protection
- gunicorn on Render for deployment (see `DEPLOY.md`)
- pytest for tests
- No JavaScript frameworks

## Roles

- **customer**: posts jobs, compares quotes, books slots, leaves reviews.
- **tradesperson**: has a `verification_status` of `pending`, `verified` or `rejected`. Only verified tradespeople can see jobs and send quotes.
- **admin**: approves or rejects tradespeople.

Customers and tradespeople have an `email_verified` field (simulated). We do NOT send real emails: the seed script sets it, and a simple "confirm email" page may flip it for the demo. Unverified customers cannot post jobs: every job-posting route must use `confirmed_customer_required` from `decorators.py`.

## Features

1. **Accounts and roles**: registration, login, role-based access.
2. **Jobs**: trade, city, description, urgency, optionally several trades.
3. **Quotes**: price, message, earliest start date. Sortable by "available soonest".
4. **Availability and booking**: tradespeople set weekly availability; customers can book a free slot for small jobs.
5. **Multi-trade project page**: accepted tradespeople see each other, propose dates and comment. The customer sees a combined timeline.
6. **Reviews**: 1-5 stars after completion. Only the customer of a job whose status is `done` can review it, only once, and only for the tradesperson who actually did the job.
7. **Seed data**: sample users, jobs and quotes for demos and development.

**Stretch goal (only after the core works)**: an AI helper on the post-a-job page that rewrites the customer's description and suggests a trade and 2-3 follow-up questions. One function, one API call, API key only as an environment variable, and it must have a fallback if the call fails.

## Code rules

- One blueprint per feature, each in its own file.
- Models live only in `models.py`.
- Every edit route has an ownership check: users only change their own data.
- Passwords are always hashed.
- A customer's phone number and address stay hidden from a tradesperson until the customer has accepted that tradesperson's quote or booked their slot. Never show them on the job list or job detail page before that.
- Collect only the data we need (data minimisation, GDPR): name, email, phone and address, because the service needs them to connect customers and tradespeople. NO ID upload; tradespeople give a qualification reference number only, no document uploads. A tradesperson's phone and address never appear on a public profile or in search.
- `flask reset-db` wipes and recreates the local database after model changes; it refuses to run when `RENDER` is set, and that guard must stay.
- Keep functions small.
- Write short comments that explain the why, not the what.

## Testing

Each feature gets at least a few pytest tests.
Email verification, contact data privacy and review integrity each need at least one pytest test.

## Secrets

Never commit secrets.
