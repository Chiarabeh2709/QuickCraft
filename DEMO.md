# QuickCraft demo

**Warning: these accounts are fake, and their password is public.** It is in this file and in the code, and it works for anyone on the demo URL. All people, companies, addresses and phone numbers are invented. Never enter real data on the demo site, and never turn this on for a site with real users.

The demo data is only created when `SEED_DEMO_DATA=true` is set and the database has no users besides the admin.

## Logins

Every demo account uses the password `demo-password-123`. The admin has its own login, set with `ADMIN_EMAIL` and `ADMIN_PASSWORD`.

| Role | Name | Email | Good for |
| --- | --- | --- | --- |
| Customer | Emma Carter (Berlin) | `emma.carter@example.com` | Posting a job, comparing quotes |
| Customer | James Walker (Hamburg) | `james.walker@example.com` | A booked slot, a cancelled job |
| Customer | Olivia Bennett (Munich) | `olivia.bennett@example.com` | Project page, booking a small job |
| Customer | Thomas Reed (Cologne) | `thomas.reed@example.com` | Comparing two quotes, a reviewed job |
| Plumber | Harbor Plumbing Ltd | `daniel.hughes@example.com` | Quoting, seeing an appointment |
| Electrician | Bright Spark Electrics | `sarah.mitchell@example.com` | A booked slot |
| Painter | Fresh Coat Painting | `michael.turner@example.com` | A received review |
| Tiler | Straight Line Tiling | `laura.collins@example.com` | Project page |
| Carpenter | Solid Oak Carpentry | `peter.morgan@example.com` | Project page, open shelves job |
| Heating engineer | Warm Home Heating | `rachel.foster@example.com` | Quoting on the boiler job |
| Plumber and heating engineer | All Round Home Services | `andrew.price@example.com` | Several trades |
| Roofer, carpenter pending | Top Floor Roofing | `helen.ward@example.com` | A trade waiting for approval |
| Painter, account pending | New Start Decorating | `mark.evans@example.com` | An account waiting for approval |

## Click path for the presentation

1. **Customer posts a job.** Log in as Emma. Click **Post a job**. Choose the trade *Plumber* and the city *Berlin*. Use the English city spelling, because jobs and tradespeople are matched on the exact name.
2. **Tradesperson quotes.** Log in as Daniel (Harbor Plumbing Ltd). Click **Open jobs**, open the new job and send a quote. The customer's phone and address are still hidden.
3. **Customer accepts and confirms an appointment.** Log in as Emma. Open **My jobs**, then **View quotes**, and click **Accept**. Choose a Wednesday at least two weeks ahead at 09:00, which is inside one of Daniel's listed slots, and click **Accept quote**. The tradesperson can now see the customer's contact details on the job page.
4. **Appointment.** The date and time now appear on Emma's job page, in **My jobs** and under **My appointments**. Log in as Daniel: he sees it under **My appointments** and on the job page. To show the double-booking check, log in as Emma again, open **View quotes** on *Full bathroom renovation*, click **Accept** on Harbor Plumbing Ltd and enter the same Wednesday at 10:00: the form refuses it and keeps your input.
5. **Cancelling.** Under **My appointments**, Emma or Daniel can click **Cancel appointment** on *Replace the old shower*. The job and the quote stay, the time is free again, and the job shows *Appointment needed*. Emma clicks **Choose a new appointment** and picks a new time with the same form. Cancelling a booked slot works differently: log in as James and cancel *Replace two light switches*, and the small job opens again.
6. **Project page.** Log in as Olivia, click **My jobs**, then **Project page** on *Kitchen makeover*. It shows her kitchen job with its tiler and carpenter, their dates and their comments. The page uses the job's description as its heading, not the title *Kitchen makeover*.
7. **Review.** Log in as Emma, click **My jobs**, then **Mark as done and review** on the job from step 1. Click **Mark job as done**, then submit a review. To show a finished example, log in as Thomas, click **My jobs**, then **Reviews** on *Repaint the bedroom*.

## Other useful pages

| Page | Log in as | Shows |
| --- | --- | --- |
| **Find tradespeople** | Any demo customer | Search by trade and city |
| **Tradespeople reviews** | Any demo customer | List with ratings |
| **Approve tradespeople and trades** | Admin | One pending account and one pending trade |
| **My jobs**, then **View quotes** on *Service the boiler before winter* | Thomas | Two quotes to sort by soonest or by price |
| **My jobs**, then **Book a slot** on *Fix a dripping bathroom tap* | Olivia | Free slots for a small job |

## Good to know

- **Dates** are set relative to the day the data is created.
- **Render's free plan** starts with an empty database after every restart, so the data is recreated and anything clicked during a demo is lost. A restart can happen at any time, and after 15 minutes without traffic the service spins down.
- **Locally**, run `flask reset-db` to start over with fresh demo data.
