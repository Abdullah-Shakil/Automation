"""Canonical profile metadata for Leadlane bots, workers, APIs, and schedulers.

Inspired by Find's profiles.py — rich overlay + dedicated profile pages.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Collect bots (data sources)
# ---------------------------------------------------------------------------

BOT_PROFILES: dict[str, dict[str, Any]] = {
    "companies_house": {
        "role": "UK company register",
        "origin": "Official UK Companies House Public Data API",
        "source_name": "Companies House",
        "source_url": "https://developer.company-information.service.gov.uk/",
        "docs_url": "https://developer-specs.company-information.service.gov.uk/",
        "console_url": "https://developer.company-information.service.gov.uk/",
        "signup_url": "https://developer.company-information.service.gov.uk/",
        "signup_label": "Developer Hub",
        "env_name": "COMPANIES_HOUSE_API_KEY",
        "profile_summary": (
            "Searches active England companies by SIC / keyword for the Find trades. "
            "Stores company number, office address, officers (later enrich)."
        ),
        "free_tier": "600 requests / 5 minutes",
        "how_to": (
            "1) Open Companies House Developer Hub and sign in\n"
            "2) Create an application\n"
            "3) Copy the API key\n"
            "4) Paste into .env and GitHub secrets as COMPANIES_HOUSE_API_KEY="
        ),
    },
    "overpass": {
        "role": "OpenStreetMap places",
        "origin": "OpenStreetMap / Overpass + Nominatim",
        "source_name": "OpenStreetMap",
        "source_url": "https://www.openstreetmap.org/",
        "docs_url": "https://wiki.openstreetmap.org/wiki/Overpass_API",
        "console_url": "https://overpass-turbo.eu/",
        "signup_url": "",
        "signup_label": "",
        "env_name": "",
        "profile_summary": (
            "Reads tagged map features across England (craft=plumber, office=lawyer, …). "
            "No API key. Respects USER_AGENT. Map data © OpenStreetMap contributors (ODbL)."
        ),
        "free_tier": "Fair-use public Overpass",
        "how_to": "Set USER_AGENT in .env to a real contact string. No key required.",
    },
    "wikidata": {
        "role": "Wikidata SPARQL",
        "origin": "Wikimedia Wikidata Query Service",
        "source_name": "Wikidata",
        "source_url": "https://www.wikidata.org/",
        "docs_url": "https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service",
        "console_url": "https://query.wikidata.org/",
        "signup_url": "",
        "signup_label": "",
        "env_name": "",
        "profile_summary": (
            "One SPARQL step per trade keyword for UK businesses with labels, phone, website, address."
        ),
        "free_tier": "Public SPARQL (rate limited)",
        "how_to": "No key. USER_AGENT must not use example.com.",
    },
    "serper": {
        "role": "Google search via Serper",
        "origin": "serper.dev Google Search API",
        "source_name": "Serper",
        "source_url": "https://serper.dev/",
        "docs_url": "https://serper.dev/",
        "console_url": "https://serper.dev/",
        "signup_url": "https://serper.dev/",
        "signup_label": "serper.dev",
        "env_name": "SERPER_API_KEY",
        "profile_summary": (
            "Places + organic search for UK trades. Also available as a Runner that drives this bot and others."
        ),
        "free_tier": "Leadlane caps 100 searches / UK day",
        "how_to": (
            "1) Sign up at serper.dev\n"
            "2) Copy API key\n"
            "3) SERPER_API_KEY= in .env and GitHub secrets\n"
            "4) Start the Serper runner on the Runners tab"
        ),
    },
    "tavily": {
        "role": "Tavily AI search",
        "origin": "Tavily Search API",
        "source_name": "Tavily",
        "source_url": "https://tavily.com/",
        "docs_url": "https://docs.tavily.com/",
        "console_url": "https://app.tavily.com/home",
        "signup_url": "https://app.tavily.com/home",
        "signup_label": "Tavily dashboard",
        "env_name": "TAVILY_API_KEY",
        "profile_summary": "UK trade search pages from Tavily. Start the Tavily runner to collect with this bot.",
        "free_tier": "Leadlane caps 50 searches / UK day",
        "how_to": "Create a key in the Tavily dashboard → TAVILY_API_KEY= → Start Tavily on Runners.",
    },
    "serpapi": {
        "role": "SerpApi Google SERP",
        "origin": "serpapi.com",
        "source_name": "SerpApi",
        "source_url": "https://serpapi.com/",
        "docs_url": "https://serpapi.com/search-api",
        "console_url": "https://serpapi.com/manage-api-key",
        "signup_url": "https://serpapi.com/",
        "signup_label": "serpapi.com",
        "env_name": "SERPAPI_API_KEY",
        "profile_summary": "Organic / Maps results for UK trades. Start the SerpApi runner to collect.",
        "free_tier": "Leadlane caps 20 searches / UK day",
        "how_to": "Sign up → copy key → SERPAPI_API_KEY= → Start SerpApi on Runners.",
    },
    "google_places": {
        "role": "Google Places text search",
        "origin": "Google Maps Platform Places API",
        "source_name": "Google Places",
        "source_url": "https://console.cloud.google.com/google/maps-apis",
        "docs_url": "https://developers.google.com/maps/documentation/places/web-service/text-search",
        "console_url": "https://console.cloud.google.com/google/maps-apis",
        "signup_url": "https://console.cloud.google.com/google/maps-apis",
        "signup_label": "Google Cloud Console",
        "env_name": "GOOGLE_PLACES_API_KEY",
        "profile_summary": (
            "Official Places text search for UK trades — phone, website, address when Google has them. "
            "From Information Hunters catalogue."
        ),
        "free_tier": "Maps monthly credit · Leadlane 100 / day",
        "how_to": (
            "1) Enable Places API (New) in Google Cloud\n"
            "2) Create an API key\n"
            "3) GOOGLE_PLACES_API_KEY= in .env and GitHub secrets"
        ),
    },
    "scrapingbee": {
        "role": "Contact unlocker · page fetch",
        "origin": "ScrapingBee HTML API",
        "source_name": "ScrapingBee",
        "source_url": "https://www.scrapingbee.com/",
        "docs_url": "https://www.scrapingbee.com/documentation/",
        "console_url": "https://app.scrapingbee.com/account/login",
        "signup_url": "https://app.scrapingbee.com/",
        "signup_label": "ScrapingBee",
        "env_name": "SCRAPINGBEE_API_KEY",
        "profile_summary": "Fetches public business pages when direct HTTP is blocked. Not a discovery bot.",
        "free_tier": "~1,000 free credits",
        "how_to": "Sign up → copy API key → SCRAPINGBEE_API_KEY=",
    },
    "apify": {
        "role": "Contact unlocker · actor",
        "origin": "Apify platform",
        "source_name": "Apify",
        "source_url": "https://apify.com/",
        "docs_url": "https://docs.apify.com/",
        "console_url": "https://console.apify.com/",
        "signup_url": "https://console.apify.com/account/integrations",
        "signup_label": "Apify console",
        "env_name": "APIFY_TOKEN",
        "profile_summary": "Optional actor enrichment. Also usable as a Cloud host (LEADLANE_APIFY_HOST).",
        "free_tier": "~$5 compute / month free",
        "how_to": "Create token → APIFY_TOKEN= (optional APIFY_ACTOR_ID=)",
    },
}

# ---------------------------------------------------------------------------
# Collect runners (drive bots) + AI assist
# ---------------------------------------------------------------------------

WORKER_PROFILES: dict[str, dict[str, Any]] = {
    "serper": {
        **BOT_PROFILES["serper"],
        "role": "Collect runner · Serper",
        "profile_summary": (
            "Startable runner. When running, Cloud steps the Serper bot "
            "and also cycles other bots with remaining quota."
        ),
    },
    "tavily": {
        **BOT_PROFILES["tavily"],
        "role": "Collect runner · Tavily",
        "profile_summary": (
            "Startable runner. Cloud cycles Tavily and other bots while this runner is started."
        ),
    },
    "serpapi": {
        **BOT_PROFILES["serpapi"],
        "role": "Collect runner · SerpApi",
        "profile_summary": (
            "Startable runner. Cloud cycles SerpApi and other bots while this runner is started."
        ),
    },
    "gemini": {
        "role": "AI assist (no inventing leads)",
        "origin": "Google AI Studio",
        "source_name": "Google Gemini",
        "source_url": "https://aistudio.google.com/",
        "docs_url": "https://ai.google.dev/gemini-api/docs",
        "console_url": "https://aistudio.google.com/apikey",
        "signup_url": "https://aistudio.google.com/apikey",
        "signup_label": "Google AI Studio",
        "env_name": "GEMINI_API_KEY",
        "profile_summary": "Optional AI assist only. Leadlane does not invent business contacts from Gemini.",
        "free_tier": "Free tier model limits",
        "how_to": "Create a key in Google AI Studio → GEMINI_API_KEY=",
        "can_collect": False,
    },
    "groq": {
        "role": "AI assist (no inventing leads)",
        "origin": "GroqCloud",
        "source_name": "Groq",
        "source_url": "https://console.groq.com/",
        "docs_url": "https://console.groq.com/docs",
        "console_url": "https://console.groq.com/keys",
        "signup_url": "https://console.groq.com/keys",
        "signup_label": "GroqCloud console",
        "env_name": "GROQ_API_KEY",
        "profile_summary": "Optional AI assist only. Not used to invent leads.",
        "free_tier": "Free daily model limits",
        "how_to": "Create a key in Groq console → GROQ_API_KEY=",
        "can_collect": False,
    },
}

# ---------------------------------------------------------------------------
# Cloud schedulers (wake GitHub Actions)
# ---------------------------------------------------------------------------

SCHEDULER_PROFILES: dict[str, dict[str, Any]] = {
    "github_schedule": {
        "role": "Cloud scheduler",
        "origin": "GitHub Actions schedule + workflow_dispatch",
        "source_name": "GitHub Actions",
        "source_url": "https://docs.github.com/en/actions",
        "docs_url": "https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows",
        "console_url": "",
        "profile_summary": (
            "Runs python -m app.worker with LEADLANE_CLOUD_WORKER=1. "
            "Turn on with repository variable ENABLE_SCHEDULE=true. "
            "Use the Helpers tab for cron-job.org — it only wakes this collector more often."
        ),
        "free_tier": "Public unlimited / private 2,000 min/mo",
        "how_to": "Set ENABLE_SCHEDULE=true. Put DATABASE_URL and API keys in Actions secrets. Cron-job.org setup is under Helpers.",
        "env_name": "ENABLE_SCHEDULE",
    },
    "cronjob_org": {
        "role": "GitHub helper",
        "origin": "cron-job.org → GitHub workflow_dispatch",
        "source_name": "cron-job.org",
        "source_url": "https://cron-job.org/",
        "docs_url": "https://cron-job.org/en/help/",
        "console_url": "https://console.cron-job.org/",
        "profile_summary": (
            "HTTPS cron that POSTs workflow_dispatch so GitHub Actions runs on a reliable interval. "
            "It does not collect leads — GitHub does."
        ),
        "free_tier": "Free HTTPS cron",
        "how_to": "Create a fine-grained GitHub token → store in cron-job.org → set LEADLANE_CRONJOB_ORG=true. Prefer every 15 minutes on a public repo.",
        "env_name": "LEADLANE_CRONJOB_ORG",
    },
    "cloudflare": {
        "role": "Cloud scheduler",
        "origin": "Cloudflare Workers Cron → GitHub dispatch",
        "source_name": "Cloudflare Workers",
        "source_url": "https://developers.cloudflare.com/workers/",
        "docs_url": "https://developers.cloudflare.com/workers/configuration/cron-triggers/",
        "console_url": "https://dash.cloudflare.com/",
        "profile_summary": "Cron Trigger only dispatches GitHub Actions. Does not run the Python collector.",
        "free_tier": "Workers Free cron",
        "how_to": "Deploy cloud/cloudflare with GH_DISPATCH_TOKEN → LEADLANE_CLOUDFLARE_CRON=true.",
        "env_name": "LEADLANE_CLOUDFLARE_CRON",
    },
    "vercel": {
        "role": "Cloud host",
        "origin": "Vercel Hobby cron → GitHub dispatch",
        "source_name": "Vercel",
        "source_url": "https://vercel.com/",
        "docs_url": "https://vercel.com/docs/cron-jobs",
        "console_url": "https://vercel.com/dashboard",
        "profile_summary": "Hobby cron at most once a day. Weak 24/7 trigger.",
        "free_tier": "Hobby cron (daily)",
        "how_to": "Copy cloud/vercel example → GH_DISPATCH_TOKEN → LEADLANE_VERCEL_CRON=true.",
        "env_name": "LEADLANE_VERCEL_CRON",
    },
    "gcp": {
        "role": "Cloud host",
        "origin": "Google Cloud Run Jobs + Scheduler",
        "source_name": "Google Cloud Run",
        "source_url": "https://console.cloud.google.com/run",
        "docs_url": "https://cloud.google.com/run/docs/create-jobs",
        "console_url": "https://console.cloud.google.com/run",
        "profile_summary": "Free Cloud Run Jobs allowance can wake the collector while your PC is off.",
        "free_tier": "240k vCPU-seconds / month (US)",
        "how_to": "Deploy a Job or GitHub dispatcher → LEADLANE_GCP_CRON=true.",
        "env_name": "LEADLANE_GCP_CRON",
    },
    "render": {
        "role": "Cloud host",
        "origin": "Render free web service",
        "source_name": "Render",
        "source_url": "https://render.com/",
        "docs_url": "https://render.com/docs/free",
        "console_url": "https://dashboard.render.com/",
        "profile_summary": "Free web services sleep without traffic — better as a wake-up ping than a 24/7 worker.",
        "free_tier": "750 instance hours / month",
        "how_to": "Deploy a free service that dispatches GitHub → LEADLANE_RENDER=true.",
        "env_name": "LEADLANE_RENDER",
    },
    "apify_host": {
        "role": "Cloud host",
        "origin": "Apify actor schedule",
        "source_name": "Apify",
        "source_url": "https://apify.com/",
        "docs_url": "https://docs.apify.com/",
        "console_url": "https://console.apify.com/",
        "profile_summary": "Actor can poll or dispatch GitHub. Also add APIFY_TOKEN on the Bots tab.",
        "free_tier": "~$5 compute / month",
        "how_to": "APIFY_TOKEN + APIFY_ACTOR_ID → LEADLANE_APIFY_HOST=true.",
        "env_name": "LEADLANE_APIFY_HOST",
    },
    "deno": {
        "role": "Cloud scheduler (needs card)",
        "origin": "Deno Deploy",
        "source_name": "Deno Deploy",
        "source_url": "https://deno.com/deploy",
        "docs_url": "https://docs.deno.com/deploy/",
        "console_url": "https://dash.deno.com/",
        "profile_summary": "Left off — free limits stay locked until org verification with a card.",
        "free_tier": "Needs card verification",
        "how_to": "Do not enable from the dashboard.",
        "env_name": "",
    },
    "koyeb": {
        "role": "Cloud host (needs card)",
        "origin": "Koyeb",
        "source_name": "Koyeb",
        "source_url": "https://www.koyeb.com/",
        "docs_url": "https://www.koyeb.com/docs",
        "console_url": "https://app.koyeb.com/",
        "profile_summary": "Left off — asks for a card; free instance scales to zero.",
        "free_tier": "Needs card",
        "how_to": "Do not deploy a collector here.",
        "env_name": "",
    },
    "oracle": {
        "role": "Cloud host (needs card)",
        "origin": "Oracle Cloud Always Free",
        "source_name": "Oracle Cloud",
        "source_url": "https://www.oracle.com/cloud/free/",
        "docs_url": "https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm",
        "console_url": "https://cloud.oracle.com/",
        "profile_summary": "Left off — signup asks for a card; capacity often unavailable.",
        "free_tier": "Needs card",
        "how_to": "Use GitHub Actions instead.",
        "env_name": "",
    },
}


def profile_for(kind: str, key: str) -> dict[str, Any]:
    kind = (kind or "").strip().lower()
    key = (key or "").strip().lower()
    if kind in {"bot", "bots"}:
        return dict(BOT_PROFILES.get(key) or {})
    if kind in {"worker", "workers"}:
        return dict(WORKER_PROFILES.get(key) or BOT_PROFILES.get(key) or {})
    if kind in {"api", "apis", "scheduler", "schedulers", "runner", "runners"}:
        return dict(SCHEDULER_PROFILES.get(key) or WORKER_PROFILES.get(key) or BOT_PROFILES.get(key) or {})
    return dict(WORKER_PROFILES.get(key) or BOT_PROFILES.get(key) or SCHEDULER_PROFILES.get(key) or {})


def merge_profile(kind: str, key: str, row: dict[str, Any]) -> dict[str, Any]:
    """Attach Find-style profile fields onto a dashboard / overlay payload."""
    meta = profile_for(kind, key)
    out = dict(row)
    out["kind"] = kind if kind in {"bot", "worker", "scheduler", "api"} else out.get("kind") or kind
    out["profile_summary"] = meta.get("profile_summary") or out.get("description") or ""
    out["role"] = meta.get("role") or out.get("role") or ""
    out["origin"] = meta.get("origin") or ""
    out["source_name"] = meta.get("source_name") or out.get("label") or out.get("name") or key
    out["source_url"] = meta.get("source_url") or out.get("signup_url") or ""
    out["docs_url"] = meta.get("docs_url") or ""
    out["console_url"] = meta.get("console_url") or meta.get("signup_url") or ""
    out["signup_url"] = out.get("signup_url") or meta.get("signup_url") or ""
    out["signup_label"] = out.get("signup_label") or meta.get("signup_label") or "Open site"
    out["env_name"] = out.get("env_name") or meta.get("env_name") or ""
    out["free_tier"] = meta.get("free_tier") or out.get("quota_title") or ""
    out["how_to"] = meta.get("how_to") or ""
    return out
