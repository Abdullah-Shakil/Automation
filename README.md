# Leadlane

Leadlane stores UK company records for trades you choose: plumbers, gardeners, solicitors, electricians, painters, and any profession you add. Each **bot** is one collection: professions, an area, and a data source. A background worker runs the bot until that source's free quota is used, then pauses and continues when the quota resets. Closing the browser does not stop it.

Records stay in the database. There is no email drafting and no CSV export.

## Run it locally

Python 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
mkdir -p data
python -m app.worker
```

In a second terminal, with the same virtualenv:

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and sign in with `admin` / `changeme`. Add a bot, then leave the page. The worker keeps going. Stop and start from the bot's page; the saved position is kept.

Local SQLite is enough when the website and the worker are on the same machine. Use Postgres when they are separate processes.

```bash
pytest
```

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

## Deploy so it runs with your PC off

The reliable free setup is an **Oracle Cloud Always Free** virtual machine. The website, the worker, and Postgres all run on that VM via Docker Compose. The VM stays up when your computer is off.

Checked against Oracle's Always Free documentation: an Always Free tenancy can run Ampere A1 (Arm) at **2 OCPUs and 12 GB RAM** total (1,500 OCPU-hours and 9,000 GB-hours a month), or up to two AMD micro instances (1 GB RAM each), plus 200 GB of block storage. Stay inside 2 OCPU / 12 GB. A larger shape on a paid account can be billed. Creating an Arm instance often fails with "out of host capacity"; retry another availability domain or time of day. The AMD micro shape is a smaller fallback and is tight for Postgres plus two Python processes.

These were not chosen as the always-on path:

- **Render and Railway free web services sleep**, and a separate worker is not included free. A sleeping site cannot keep collection running.
- **Koyeb's free instance** is one small service, not a durable separate worker.
- **GitHub Actions plus Neon or Supabase** can run collection in bursts. Actions minutes on a private repository are capped (2,000 a month), scheduled runs can be skipped or delayed, Supabase free projects pause after about a week without use, and Neon compute suspends until the next connection. The workflow in `.github/workflows/collect.yml` is a manual fallback (`workflow_dispatch`, `python -m app.worker --max-seconds 480`), not the way to keep bots running all day. Point `DATABASE_URL` at the free Postgres if you use it. The website still needs a host; the Oracle VM already provides one.

`python:3.12-slim` runs on Arm and AMD.

### 1. Create the VM

1. Create an Oracle Cloud account and open the Always Free path. Do not upgrade the shape past the free allowance.
2. Create a VCN with a public subnet if the wizard offers one.
3. Create a compute instance:
   - Image: Ubuntu 22.04 or 24.04, the **aarch64** build if you chose Ampere.
   - Shape: `VM.Standard.A1.Flex` with **2 OCPUs and 12 GB** (or less, still inside the free total). If capacity is unavailable, try another availability domain, or `VM.Standard.E2.1.Micro`.
   - Add your SSH public key.
   - Assign a public IP.
4. In the subnet **security list** (and the network security group, if you created one), add an ingress rule: TCP port **8000**, source `0.0.0.0/0`. Port 22 should already be open for SSH.
5. Note the public IP.

### 2. Open port 8000 on the instance itself

Oracle's security list is not enough. Ubuntu also filters ports.

```bash
ssh ubuntu@YOUR_PUBLIC_IP
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT
sudo apt-get update
sudo apt-get install -y iptables-persistent
sudo netfilter-persistent save
```

If `iptables-persistent` asks to save current rules, say yes. Confirm with `sudo iptables -L INPUT -n | head`.

### 3. Install Docker

```bash
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker ubuntu
```

Log out and back in so the `docker` group applies.

### 4. Configure and start

```bash
git clone YOUR_REPOSITORY_URL leadlane
cd leadlane
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edit `.env`:

- `ENVIRONMENT=production`
- `SECRET_KEY` to the random string you just printed
- `ADMIN_USERNAME` and `ADMIN_PASSWORD` to something that is not `admin` / `changeme`
- `USER_AGENT` to a name plus a contact address you control
- `COMPANIES_HOUSE_API_KEY` if you want that source
- Leave `GOOGLE_PLACES_ENABLED=false` unless you accept the charge risk

`docker-compose.yml` points the web and worker at Postgres on the VM and overrides `DATABASE_URL`. The database volume survives restarts.

```bash
docker compose up -d --build
docker compose ps
```

Open `http://YOUR_PUBLIC_IP:8000`. Sign in, add a bot, and confirm the worker line says it is running. `docker compose logs -f worker` shows each step. `docker compose restart` keeps bot status, checkpoints, leads, and quotas because they live in Postgres.

To update later: `git pull` and `docker compose up -d --build`.

## Sources in more detail

**Companies House.** Advanced search of active companies by SIC code, or by name keyword when a trade has no SIC code, plus the location you typed. Stored description is the company type and SIC codes when the register returns them. Phone, website, and email are not on this API.

**OpenStreetMap.** Nominatim turns the area into a box, then Overpass reads tagged places (`craft=plumber`, `office=lawyer`, and so on). Phone, website, email, and description are stored only when a mapper wrote them. A mapper note is ignored. Map data is © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright). Companies House data is under the [Open Government Licence](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

**Wikidata.** One SPARQL query per step for UK businesses (`instance of` business, `country` United Kingdom) whose English label contains the trade keyword and whose label, description, or address mentions the place.

**Directory.** Set `DIRECTORY_SEARCH_URL_TEMPLATE` with `{keyword}`, `{location}`, and `{page}`. If `robots.txt` disallows the URL, the bot stops and does not fetch the page.

**Google Places.** Optional. Field mask is id, name, address, national phone, website, and maps link. Reviews and editorial summaries are not requested.

## Professions

Built in: plumbers, electricians, gardeners, solicitors, painters and decorators, builders, roofers, carpenters, plasterers, locksmiths, accountants, cleaners, estate agents, and vehicle mechanics. Add another from the Professions page with keywords, optional SIC codes, and optional OpenStreetMap tags (`craft=glazier`). A running bot keeps the copy of the profession it started with.
