# Deploying QuickCraft on Render

Render does not use a Procfile, so the start command is set in the dashboard (step 3).
The Python version comes from `.python-version`.

## Before you start

Know this about the **Free** instance type: Render deletes local files whenever the service
redeploys, restarts or spins down, and it spins down after 15 minutes without traffic.
Our SQLite database is a local file, so **every sign-up and approval can disappear**.
The admin account survives, because the app recreates it from the environment at startup.
If data has to last, follow "Keeping the data" below.

## Steps

1. Push your code to GitHub.
2. In the [Render Dashboard](https://dashboard.render.com), click **New > Web Service** and connect this repository.
3. Enter these values:

   | Field | Value |
   | --- | --- |
   | Language | `Python 3` |
   | Branch | `main` |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `gunicorn --workers 1 "app:create_app()"` |
   | Instance Type | `Free` |

   One worker on purpose: every worker runs the startup code, and SQLite handles one writer at a time.

4. Under **Environment Variables**, add:

   | Key | Value |
   | --- | --- |
   | `SECRET_KEY` | Output of `python3 -c "import secrets; print(secrets.token_hex(32))"` |
   | `ADMIN_EMAIL` | The email you will log in with as admin |
   | `ADMIN_PASSWORD` | A long, unique password |

   These live only in Render. Never commit them. Changing `ADMIN_PASSWORD` later does not
   change the password of an admin who already exists in the database.

5. Click **Create Web Service** and wait for the build to finish. The URL ends in `onrender.com`.

Every push to `main` now deploys automatically. If a build fails, the previous version keeps running.

## Check that it works

1. Open the URL. The first request after a spin-down can take a while.
2. Sign up as a tradesperson. The page should say your account is waiting for approval.
3. Log out, then log in with `ADMIN_EMAIL` and `ADMIN_PASSWORD`.
4. Approve the tradesperson under **Pending tradespeople**.

If the service fails to start, look at **Logs** in the dashboard. `SECRET_KEY is not set` means step 4 was missed.

## Keeping the data

Persistent disks need a paid instance type (see Render's pricing page for the cost).

1. Under **Settings > Instance Type**, choose a paid type.
2. Under **Disks**, add a disk with the mount path `/var/data`.
3. Add the environment variable `DATABASE_URL` with the value `sqlite:////var/data/quickcraft.db` (four slashes).

With a disk, each deploy has a short downtime and the service cannot scale to several instances.

## After changing a model

The app creates missing tables at startup, but it does not change existing ones.
On a Free instance the database starts fresh anyway. With a disk, you have to update or
replace the database file yourself.
