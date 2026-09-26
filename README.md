# Leadlane

Leadlane stores UK company records for trades you choose: plumbers, gardeners, solicitors, electricians, painters, and any profession you add. Each **bot** is one collection: professions, an area, and a data source. The bot runs until that source's free quota is used, then pauses and continues when the quota resets.

The dashboard runs on your PC and does not ask you to sign in. The collector is a scheduled GitHub Actions job, so bots keep working with the PC off. Both talk to the same free cloud Postgres. Records stay in that database. There is no email drafting and no CSV export.

Do not expose the dashboard on the public internet. There is no login.

## Run the dashboard on this PC

Python 3.12. For a trial with everything on one machine, leave `DATABASE_URL` on SQLite and start a local worker as well. To collect with the PC off, point `DATABASE_URL` at the cloud database from the next section and do not rely on the local worker.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
mkdir -p data
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Add a bot, then start and stop it from its page. The saved position is kept.

Optional local worker, same database as the dashboard:

```bash
source .venv/bin/activate
python -m app.worker
```

```bash
pytest
```

## Cloud database and scheduled worker

The dashboard stays on your PC. Postgres and the worker do not.

**Neon** is the database to use. The free plan suspends compute when it is idle and wakes on the next connection, which suits a dashboard you open sometimes and a job that runs every few hours. **Supabase** free Postgres also works, but a free project pauses after about a week without activity. The schedule below keeps it awake only while GitHub Actions keeps running.

GitHub Actions runs the worker. On a private repository the free plan includes 2,000 minutes a month. The workflow is every 3 hours, exits when nothing is due, and stops after 8 minutes if bots are still running. That stays inside the allowance when runs are short. A public repository currently includes Actions minutes for public use; check the repository billing page if you are close to a cap. Scheduled runs can be delayed or skipped. Bot status, checkpoints, leads, errors, and quotas live in Postgres, so the next run continues where the last one stopped. This is periodic collection, not a process that sits in a loop all day.

These were not used for the website, because the dashboard is local:

- Render and Railway free web services sleep, and a separate worker is not included free.
- Koyeb's free instance is one small service, not a separate scheduled worker.
- An Oracle Cloud Always Free VM can run all day (Ampere A1 at 2 OCPUs and 12 GB, or a 1 GB AMD micro), but capacity is often unavailable and the instance is more work than this setup.

### 1. Create the free database

1. Create a [Neon](https://neon.tech) account and a free project. Copy the connection string. It looks like `postgresql://USER:PASSWORD@HOST/neondb?sslmode=require`.
2. Or create a [Supabase](https://supabase.com) project and copy the URI from Project Settings → Database. Prefer the session pooler URI if Supabase shows one. Keep `sslmode=require`.

Leadlane rewrites `postgres://` and `postgresql://` to `postgresql+psycopg://`. You can paste the host's string as-is.

### 2. Point the dashboard at it

In `.env` on your PC:

```bash
DATABASE_URL=postgresql://USER:PASSWORD@HOST/neondb?sslmode=require
USER_AGENT=Leadlane/1.0 (UK small-business research; contact: you@yourdomain)
COMPANIES_HOUSE_API_KEY=
GOOGLE_PLACES_ENABLED=false
```

`USER_AGENT` must not use `example.com`. Nominatim and Wikidata reject that.

Start the dashboard with `uvicorn` bound to `127.0.0.1` only, as above. Add a bot. Nothing collects until a worker runs.

### 3. Schedule the worker

1. Push this repo to GitHub.
2. Settings → Secrets and variables → Actions. Add:
   - `DATABASE_URL` — the same string as on your PC
   - `USER_AGENT` — the same identifying string
   - `COMPANIES_HOUSE_API_KEY` — optional; leave the secret empty or omit it
   - `GOOGLE_PLACES_ENABLED` — `false` unless you accept the charge risk below
   - `GOOGLE_PLACES_API_KEY` — only if you enabled Places
   - `DIRECTORY_SEARCH_URL_TEMPLATE` — optional
3. Actions → Collect → enable workflows if GitHub asks. The file `.github/workflows/collect.yml` runs every 3 hours and can also be started with "Run workflow".
4. Run it once by hand. The log should say the worker started and, if a bot is running, that it took a step. Then close your PC. The next scheduled run continues that bot until the source quota is used, and resumes after the quota resets.
5. Open the dashboard later. It reads the same database: leads, the bot's error log, and performance.

`docker-compose.yml` is only for trying the web process, a worker, and Postgres together on your PC. It is not the cloud setup, and it should not be published to the internet.

## What a bot stores

Name, profession, description when the source has one, address, phone, website, a publicly listed email, source, source URL, and the date found. The same business found twice is one row. Empty fields can be filled from a later sighting. Nothing is invented: there is no guessed `info@` address, and Companies House officer records are not requested.

## Free quotas

Every bot stops at the source's free allowance. The allowance is shared by every bot on that source. You cannot raise it from the environment.

| Source | Free quota | Resets | Key |
| --- | --- | --- | --- |
| Companies House | 600 requests / 5 minutes | End of the fixed UTC block (00–05, 05–10, …) | Free API key |
| OpenStreetMap | 100 requests / day | Midnight, Europe/London | None |
| Wikidata | 60 requests / day | Midnight, Europe/London | None |
| Directory page | 200 fetches / day | Midnight, Europe/London | None |
| Google Places | 1,000 requests / month | Midnight, US Pacific | Off unless you opt in |

Companies House publishes 600 requests per 5-minute period per key. Fixed UTC blocks stay inside that rule. The key is free at [Companies House for developers](https://developer.company-information.service.gov.uk/).

OpenStreetMap's public Overpass instance allows one-off use up to about 10,000 queries a day. Regular automated use should stay around a hundredth of that, so Leadlane counts Nominatim geocoding and Overpass queries together toward 100 requests a UK day. The User-Agent must identify the app. `example.com` is rejected.

Wikidata Query Service has no billed quota. Fair use is on the order of 30 queries a minute, with a 60-second timeout and a required User-Agent. Sixty queries a day keeps a background bot light. Wikidata rarely lists a local plumber; better-known organisations are more likely.

The directory adapter is not tied to one site. It reads a search URL you set, checks `robots.txt`, honours crawl-delay, and only stores schema.org business details. The 200-page cap is politeness, not a vendor bill.

Social networks (Facebook, Instagram, LinkedIn, TikTok, Nextdoor) have no official free API for this search. They are listed in the form and never fetched.

### Google Places can charge

Google Places stays **off** until `GOOGLE_PLACES_ENABLED=true` and `GOOGLE_PLACES_API_KEY` are both set. Google requires a billing account on the Cloud project before any call succeeds.

Since March 2025 the old $200 monthly credit is a free call allowance per SKU: about 10,000 Essentials, 5,000 Pro, and 1,000 Enterprise calls a month. Requesting a phone number or website makes Text Search an **Enterprise** call. Leadlane asks for those fields, so the bot stops at 1,000 requests a month (midnight Pacific time), inside that free allowance.

Past that cap, Google charges. Other Google products on the same project can charge even if this bot is inside its cap. Leave the source off unless you have checked the [Maps Platform pricing](https://developers.google.com/maps/billing-and-pricing/pricing) page and accept that.

## Sources in more detail

**Companies House.** Advanced search of active companies by SIC code, or by name keyword when a trade has no SIC code, plus the location you typed. Stored description is the company type and SIC codes when the register returns them. Phone, website, and email are not on this API.

**OpenStreetMap.** Nominatim turns the area into a box, then Overpass reads tagged places (`craft=plumber`, `office=lawyer`, and so on). Phone, website, email, and description are stored only when a mapper wrote them. A mapper note is ignored. Map data is © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright). Companies House data is under the [Open Government Licence](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

**Wikidata.** One SPARQL query per step for UK businesses (`instance of` business, `country` United Kingdom) whose English label contains the trade keyword and whose label, description, or address mentions the place.

**Directory.** Set `DIRECTORY_SEARCH_URL_TEMPLATE` with `{keyword}`, `{location}`, and `{page}`. If `robots.txt` disallows the URL, the bot stops and does not fetch the page.

**Google Places.** Optional. Field mask is id, name, address, national phone, website, and maps link. Reviews and editorial summaries are not requested.

## Professions

Built in: plumbers, electricians, gardeners, solicitors, painters and decorators, builders, roofers, carpenters, plasterers, locksmiths, accountants, cleaners, estate agents, and vehicle mechanics. Add another from the Professions page with keywords, optional SIC codes, and optional OpenStreetMap tags (`craft=glazier`). A running bot keeps the copy of the profession it started with.
