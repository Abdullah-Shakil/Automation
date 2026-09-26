# Leadlane

Leadlane stores UK company records for five fixed trades: **Electricians, Gardeners, Painters, Plumbers, Solicitors**. Six shared tables hold everything: **bots**, **workers**, **logs**, **leads**, **usage**, **settings**.

**Cloud** hosts (GitHub Actions and wake-ups) keep collection going offline. **Runners** (Serper / Tavily / SerpApi) are started on the Runners tab — start any number; each drives bots until the task or quota is done. **Bots** are every API-key and free data source (meters only). **cron-job.org** only wakes GitHub; set it up under GitHub’s profile → Helpers.

The dashboard runs on your Windows PC and does not ask you to sign in. It only reads and writes the shared database. It does not collect. Collection runs in the cloud so it continues with the PC off. Records stay in the database. There is no email drafting and no CSV export.

Do not expose the dashboard on the public internet. There is no login.

## Run the dashboard on Windows

Python 3.12. Open PowerShell in this folder:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
mkdir data
.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Start and stop **workers** there. That only changes flags in the database. Nothing is collected until a cloud scheduler below is turned on and at least one worker is started.

Tests:

```powershell
.venv\Scripts\activate
pytest
```

`docker-compose.yml` can start the dashboard and Postgres on a PC. It does not start a collector, and it should not be published to the internet.

## Cloud database

The dashboard and the cloud collector use the same Postgres.

**Neon** is the database to use. The free plan suspends compute when it is idle and wakes on the next connection. **Supabase** free Postgres also works, but a free project pauses after about a week without activity. A regular collector run wakes it only while that run keeps happening.

1. Create a [Neon](https://neon.tech) project and copy the connection string (`postgresql://USER:PASSWORD@HOST/neondb?sslmode=require`).
2. Or create a [Supabase](https://supabase.com) project and use the session pooler URI (IPv4). The direct `db.` host is often IPv6-only. Set `SUPABASE_URL`, `SUPABASE_DB_PASSWORD`, and `SUPABASE_DB_REGION` in `.env`, or paste the URI into `DATABASE_URL`. The password is the database password, not the anon key. `SUPABASE_SERVICE_ROLE_KEY` is not needed.

Leadlane rewrites `postgres://` and `postgresql://` to `postgresql+psycopg://`.

In `.env` on the PC:

```powershell
DATABASE_URL=postgresql://USER:PASSWORD@HOST/neondb?sslmode=require
USER_AGENT=Leadlane/1.0 (UK small-business research; contact: you@yourdomain)
```

`USER_AGENT` must not use `example.com`. Nominatim and Wikidata reject that.

Put the same `DATABASE_URL` and `USER_AGENT` in GitHub Actions secrets (repository → Settings → Secrets and variables → Actions).

## Cloud collectors

Every scheduler is **off** until you set its switch. The Python process is always GitHub Actions running `python -m app.worker` with `LEADLANE_CLOUD_WORKER=1`. The PC cannot start that process. Other platforms only send `workflow_dispatch` so a run is not left to GitHub's delayed scheduler.

On a **private** repository, Actions includes 2,000 minutes a month. The workflow exits when nothing is due and stops after 8 minutes, and the schedule is every 3 hours. Stay on that interval. On a **public** repository, standard GitHub-hosted runners currently include unlimited Actions minutes. Check the repository billing page if that changes.

These are not used, because they are not a free always-on worker: Render, Railway, and Fly.io.

### GitHub Actions schedule — no card

1. Push the repo.
2. Add the secrets listed at the bottom.
3. Settings → Secrets and variables → Actions → Variables → `ENABLE_SCHEDULE` = `true`. Optional: `TRADE_PRESET` = `all` or one of `electrician`, `gardener`, `painter`, `plumber`, `solicitor` (used by the schedule and as the dispatch default when the dashboard has not set a Find choice).
4. In `.env` on the PC set `LEADLANE_GITHUB_SCHEDULE=true` and restart the dashboard. Workers → Use this. Set **Find** to All or one trade.
5. Actions → Collect → Run workflow once (pick the trade in the input if you want), then close the PC.

Until `ENABLE_SCHEDULE` is `true`, the 3-hour schedule is skipped. Run workflow still works.

### cron-job.org — GitHub helper (no card)

Free HTTPS cron (https://cron-job.org/en/). It does not collect leads — it only POSTs `workflow_dispatch` so GitHub Actions runs on a reliable interval. In the dashboard it lives under **GitHub Actions schedule → profile → Helpers**, not as its own scheduler row.

1. GitHub → Settings → Developer settings → Fine-grained tokens. This repository only. Permission: Actions, Read and write. Copy the token. It expires, so renew it.
2. On cron-job.org create a job (every 3 hours on a private repo):
   - URL: `https://api.github.com/repos/OWNER/REPO/actions/workflows/collect.yml/dispatches`
   - Method: POST
   - Headers: `Accept: application/vnd.github+json`, `Authorization: Bearer YOUR_TOKEN`, `Content-Type: application/json`, `X-GitHub-Api-Version: 2022-11-28`
   - Body (All trades): `{"ref":"main","inputs":{"trade_preset":"all"}}`
   - Or one trade, e.g. plumbers: `{"ref":"main","inputs":{"trade_preset":"plumber"}}`
3. The token lives in cron-job.org, not in Leadlane.
4. Set `LEADLANE_CRONJOB_ORG=true` in `.env` and choose it on the Workers tab.

### Cloudflare Workers Cron Trigger — no card

Workers Free includes Cron Triggers (5 per account) and 100,000 requests a day. A cron invocation on the free plan has 10 ms of CPU, which is enough to POST to GitHub. It cannot run the Python collector.

1. Install Wrangler and `cd cloud/cloudflare`.
2. Set secrets `GH_DISPATCH_TOKEN` (the same fine-grained token) and `GH_REPO` (`OWNER/REPO`). Optional `GH_REF` (default `main`).
3. Uncomment the cron in `wrangler.toml` (`17 */3 * * *`), then deploy.
4. Set `LEADLANE_CLOUDFLARE_CRON=true` in `.env`.

Until the cron line is uncommented and the token is set, the sample does nothing.

### Deno Deploy cron — needs a card

Deno Deploy has a free plan and `Deno.cron`, but the full free limits stay locked until the organisation is verified with a card. This option stays off in the dashboard and cannot be selected. `cloud/deno/main.ts` only dispatches GitHub Actions, and it returns immediately without `GH_DISPATCH_TOKEN`. Do not deploy it unless you accept the card check.

### Vercel cron (Hobby) — no card, once a day

The Hobby plan is free and does not need a card. Hobby cron runs at most once a day, sometime inside that hour, so it is a weak way to keep collection moving. Pro cron (once a minute) needs a card and is not used.

There is no `vercel.json` at the repo root, so deploying the repo does not start a cron. If a daily ping is enough, deploy `cloud/vercel` with `GH_DISPATCH_TOKEN` and `GH_REPO`, and copy `cloud/vercel/vercel.json.example` to `vercel.json` in that project. Then set `LEADLANE_VERCEL_CRON=true`.

### Koyeb — needs a card, left off

Koyeb asks for a card to prevent abuse. The free instance is one web service, cannot be a worker, and scales to zero after an hour without traffic. The dashboard will not turn it on.

### Oracle Cloud Always Free — needs a card, left off

An Always Free VM can run all day, but account verification asks for a card and capacity is often unavailable. The dashboard will not turn it on. GitHub Actions is the collector.

## What a company row stores

Company name, trading name, company type, company number, status, trade, SIC codes, incorporation date, landline, mobile (a UK number starting 07), email, website, address lines, town, county, postcode, officers (name, role, and appointment date only — home addresses are not stored), social profile links found on the company's own site, description, source URLs, and last updated.

The same company found twice is one row. Matches are company number, website domain, or normalised name plus postcode. Empty fields can be filled from a later sighting. Nothing is invented: there is no guessed `info@` address.

After the search bots are idle, the cloud worker fills gaps:

- Companies House officers endpoint, counted against the 600-requests-per-5-minutes quota. It pauses at the limit and resumes when the window resets.
- The company's own website, after `robots.txt`, homepage plus contact and about pages. Tel and mailto links, schema.org LocalBusiness or Organization JSON-LD, the address, and social links. 80 fetches per UK day, shared by every company, then it pauses until midnight UK time.

Errors and the website usage count are written to the database and shown on the dashboard and the company page.

## Free quotas

Every bot stops at the source's free allowance. The allowance is shared by every bot on that source. You cannot raise it from the environment.

| Source | Free quota | Resets | Key |
| --- | --- | --- | --- |
| Companies House | 600 requests / 5 minutes | End of the fixed UTC block (00–05, 05–10, …) | Free API key |
| OpenStreetMap | 100 requests / day | Midnight, Europe/London | None |
| Wikidata | 60 requests / day | Midnight, Europe/London | None |
| Serper | 100 searches / day | Midnight, Europe/London | Free trial key |
| Tavily | 50 searches / day | Midnight, Europe/London | Free plan key |
| SerpApi | 20 searches / day | Midnight, Europe/London | Free plan key |
| Company websites | 80 page fetches / day | Midnight, Europe/London | None |

Companies House publishes 600 requests per 5-minute period per key. Fixed UTC blocks stay inside that rule. The key is free at [Companies House for developers](https://developer.company-information.service.gov.uk/). Officer lookups use the same quota.

OpenStreetMap's public Overpass instance allows one-off use up to about 10,000 queries a day. Regular automated use should stay around a hundredth of that, so Leadlane counts Nominatim geocoding and Overpass queries together toward 100 requests a UK day. The User-Agent must identify the app. `example.com` is rejected.

Wikidata Query Service has no billed quota. Fair use is on the order of 30 queries a minute, with a 60-second timeout and a required User-Agent. Sixty queries a day keeps a background bot light. Wikidata rarely lists a local plumber; better-known organisations are more likely.

Serper's trial is about 2,500 queries once ([serper.dev](https://serper.dev/)). Tavily's free plan is about 1,000 credits a month ([Tavily](https://app.tavily.com/home)). SerpApi's free plan is about 250 searches a month ([serpapi.com](https://serpapi.com/)). Leadlane's daily caps are lower so a free allowance lasts.

Gemini ([Google AI Studio](https://aistudio.google.com/apikey), model limits, often about 250 requests a day) and Groq ([Groq console](https://console.groq.com/keys), model limits, often about 1,000 requests a day) are AI assist only. They do not invent contacts. Search grounding on Gemini may need a paid plan.

Social networks (Facebook, Instagram, LinkedIn, TikTok, Nextdoor) have no official free API for this search. They are listed and never fetched. Links to them are stored only when they appear on a company's own website.

## Keys and secrets

Put source keys in `.env` on the PC (so the dashboard can show Connected) and the same values in GitHub Actions secrets (so the cloud collector can call the APIs).

| Name | Where | Signup | Free limit |
| --- | --- | --- | --- |
| `DATABASE_URL` | `.env` and Actions secret | [Neon](https://neon.tech) or [Supabase](https://supabase.com) | Neon free project; Supabase free project pauses after about 7 idle days |
| `USER_AGENT` | `.env` and Actions secret | None | Identify the app. No `example.com` |
| `COMPANIES_HOUSE_API_KEY` | `.env` and Actions secret | [Companies House](https://developer.company-information.service.gov.uk/) | 600 requests / 5 minutes |
| `GOOGLE_PLACES_API_KEY` | `.env` and Actions secret | [Google Maps Platform](https://console.cloud.google.com/google/maps-apis) | Maps credit; Leadlane 100 / UK day |
| `SERPER_API_KEY` | `.env` and Actions secret | [serper.dev](https://serper.dev/) | About 2,500 trial queries; Leadlane uses 100 / UK day |
| `TAVILY_API_KEY` | `.env` and Actions secret | [Tavily](https://app.tavily.com/home) | About 1,000 credits / month; Leadlane uses 50 / UK day |
| `SERPAPI_API_KEY` | `.env` and Actions secret | [serpapi.com](https://serpapi.com/) | About 250 searches / month; Leadlane uses 20 / UK day |
| `GEMINI_API_KEY` | `.env` and Actions secret | [Google AI Studio](https://aistudio.google.com/apikey) | Model daily limit, AI assist only |
| `GROQ_API_KEY` | `.env` and Actions secret | [Groq console](https://console.groq.com/keys) | Model daily limit, AI assist only |
| `SCRAPINGBEE_API_KEY` | `.env` and Actions secret | [ScrapingBee](https://app.scrapingbee.com/) | ~1,000 free credits; used when a company site blocks direct fetch |
| `APIFY_TOKEN` | `.env` and Actions secret | [Apify](https://console.apify.com/account/integrations) | Free compute credit; optional `APIFY_ACTOR_ID` for actors |
| `ENABLE_SCHEDULE` | GitHub Actions **variable** `true` | None | Turns on the 3-hour schedule. No card |
| `GH_DISPATCH_TOKEN` | cron-job.org, Cloudflare, Deno, or Vercel only | [Fine-grained GitHub token](https://github.com/settings/personal-access-tokens) | Actions: Read and write, this repo. Not stored in Leadlane |
| `GH_REPO` | The same trigger | None | `OWNER/REPO` |
| `LEADLANE_GITHUB_SCHEDULE` | `.env` only | None | `true` after you set `ENABLE_SCHEDULE` |
| `LEADLANE_CRONJOB_ORG` | `.env` only | [cron-job.org](https://cron-job.org/en/) | `true` after the cron job exists |
| `LEADLANE_CLOUDFLARE_CRON` | `.env` only | [Cloudflare Workers](https://developers.cloudflare.com/workers/) | `true` after you deploy the cron |
| `LEADLANE_VERCEL_CRON` | `.env` only | [Vercel Hobby](https://vercel.com/docs/plans/hobby) | `true` after a daily cron is deployed |

`LEADLANE_CLOUD_WORKER` is set by the GitHub Actions workflow. Do not set it on the PC.

## Sources in more detail

**Companies House.** Advanced search of active companies by SIC code, or by name keyword when a trade has no SIC code, across England. Stored fields include company number, type, status, SIC codes, incorporation date, and the registered office split into lines, town, county, and postcode. Officers are fetched later by the cloud worker.

**OpenStreetMap.** Nominatim turns England into a box, then Overpass reads tagged places (`craft=plumber`, `office=lawyer`, and so on). Phone, website, email, and description are stored only when a mapper wrote them. A mapper note is ignored. Map data is © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright). Companies House data is under the [Open Government Licence](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

**Wikidata.** One SPARQL query per step for UK businesses (`instance of` business, `country` United Kingdom) whose English label contains the trade keyword.

**Serper / Tavily / SerpApi.** Web search APIs with free signup keys. Each step searches for a trade in England and stores organic result titles, URLs, and snippets. Nothing is invented.

## Trades

Fixed Find presets: Electricians, Gardeners, Painters, Plumbers, Solicitors (or All). Choose on the Workers tab — stored in `settings` and read by GitHub Actions / cron via `TRADE_PRESET` when provided.
