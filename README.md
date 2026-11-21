# QuickCraft

QuickCraft is a web marketplace for tradespeople. Customers post jobs or book a free slot for a small job, and verified tradespeople send quotes. Jobs that need several trades get a shared project page, and customers review the tradesperson after the job is done.

Built with Python, Flask, Jinja, Bootstrap and SQLite as a course project.

## Live demo

https://quickcraft-nc18.onrender.com/

- The site runs on Render's free plan. It sleeps after 15 minutes without traffic, so the first page load can take up to a minute.
- The database is reset and filled with fresh demo data on every restart. Anything you create during a demo can disappear.

The demo logins and a click path for the presentation are in [DEMO.md](DEMO.md).

## Source code

https://github.com/Chiarabeh2709/QuickCraft

Clone it with:

```bash
git clone https://github.com/Chiarabeh2709/QuickCraft.git
```

## Team

| Name | Student number |
| --- | --- |
| Chiara Behrens | 80988 |
| Annabelle Bechtel | 78530 |
| Jessica Davidov | 79805 |
| Sophia Sonnenberg | 82822 |

## Features

- **Roles**: customer, tradesperson and admin, with role-based access to every page.
- **Sign-up and login**: separate sign-up for customers and tradespeople, Terms of Use consent, and a simulated email confirmation. Unconfirmed customers cannot post jobs.
- **Admin verification**: the admin approves or rejects new tradespeople, and approves each additional trade separately. Only verified tradespeople and approved trades count.
- **Jobs**: customers post jobs with trade, city, description and urgency, see their own jobs and can cancel them. Verified tradespeople see open jobs that match their trades and service area.
- **Quotes and appointments**: tradespeople send quotes with price, message, earliest start and duration, and can edit or withdraw them. Customers compare quotes sorted by soonest start or by price, accept one and pick an appointment time inside the tradesperson's availability. Appointments can be cancelled and rebooked, and double bookings are refused.
- **Availability and booking**: tradespeople set weekly availability slots, and customers book a free slot for a small job.
- **Search and public profiles**: customers search tradespeople by trade and city and open public profiles with ratings. Tradespeople edit their own profile.
- **Projects**: jobs with several trades get a project page where the accepted tradespeople see each other, propose dates and comment, and the customer sees the combined schedule.
- **Messages**: customers and tradespeople write to each other per job, and the customer can share or stop sharing their phone number in a conversation.
- **Reviews**: after a job is marked as done, its customer can leave one 1-5 star review for the tradesperson who did the job.

## Run locally

You need Python 3.12. Clone the repository (see [Source code](#source-code)) and run these commands in the repository root.

Create and activate a virtual environment (macOS and Linux):

```bash
python3.12 -m venv .venv
```

```bash
source .venv/bin/activate
```

On Windows, activate it with `.venv\Scripts\activate` instead.

Install the requirements:

```bash
pip install -r requirements.txt
```

Copy the example settings:

```bash
cp .env.example .env
```

Then open `.env` and fill in:

- `SECRET_KEY`: generate one with `python -c "import secrets; print(secrets.token_hex(32))"`
- `ADMIN_EMAIL` and `ADMIN_PASSWORD`: the admin login you want to use locally
- `SEED_DEMO_DATA=true` is already set in `.env.example`. Keep it for the demo accounts from [DEMO.md](DEMO.md), or remove the line to start with an empty database.

Create the database with the admin and, if enabled, the demo data:

```bash
flask reset-db
```

This deletes all local data, so run it again whenever you want a fresh start or after a model change.

Start the app:

```bash
flask run
```

Open http://127.0.0.1:5000 in your browser.

On macOS, port 5000 is often taken by the AirPlay Receiver, and the page shows an empty "403 Forbidden". Use another port instead and open http://127.0.0.1:5001:

```bash
flask run --port 5001
```

## Run the tests

There is no separate `requirements-dev.txt`: pytest is part of `requirements.txt`, so the install step above is enough. With the virtual environment active, run:

```bash
pytest
```

The tests use an in-memory database and never touch `instance/quickcraft.db`.

## Environment variables

Set them in `.env` locally and in the Render dashboard for the live site. Never commit their values.

| Name | Purpose | Required |
| --- | --- | --- |
| `SECRET_KEY` | Signs session cookies and CSRF tokens. The app refuses to start without it. | Yes |
| `ADMIN_EMAIL` | Email of the admin account that is created at startup if it does not exist yet. | Yes, otherwise there is no admin |
| `ADMIN_PASSWORD` | Password of that admin account. Changing it later does not change an existing admin. | Yes, otherwise there is no admin |
| `SEED_DEMO_DATA` | `true` creates fake demo accounts with a public password (see DEMO.md). Only for demos, never on a site with real users. | No, off by default |
| `DATABASE_URL` | SQLAlchemy database URL. Defaults to `sqlite:///quickcraft.db`, which ends up in `instance/quickcraft.db`. | No |
| `RENDER` | Set automatically by Render. Turns on secure session cookies and blocks `flask reset-db`. Do not set it locally. | No, set by Render |

## Deployment

The live site runs on Render. The settings are:

| Field | Value |
| --- | --- |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn --workers 1 "app:create_app()"` |

One worker on purpose, because SQLite handles one writer at a time. The full step-by-step guide, the environment variables and how to keep data with a persistent disk are in [DEPLOY.md](DEPLOY.md).

## Design decisions and limits

**Privacy and data minimisation**

- We only collect the data the service needs: name, email, phone and address. There is no ID or document upload. Tradespeople give a qualification reference number only.
- Tradespeople appear under their company name in search, on public profiles and on quotes. Their phone and address never appear on a public page.
- A customer's phone number and address stay hidden from a tradesperson until the customer accepts that tradesperson's quote or books their slot. The only other way is on purpose: the customer shares their phone number in a message thread, and can stop sharing it again.
- Passwords are stored as hashes.

**Simulated parts**

- **Email confirmation**: no email is sent. A confirmation page lets the logged-in user confirm their own address.
- **Password reset**: there is no password reset. A forgotten password cannot be recovered in the app.
- **Admin check**: the admin sees the qualification reference number and decides by hand. Nothing is checked against a real register.

**Known limits**

- No service radius: jobs and tradespeople are matched on the exact city name from the tradesperson's service area.
- The free Render plan deletes the database on every restart, redeploy and spin-down.
- No real emails are sent, for confirmation or for anything else.
