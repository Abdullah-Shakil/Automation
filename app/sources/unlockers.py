"""Optional contact-fetch / unlocker bots (API keys). They do not discover leads alone."""

from app.config import Settings
from app.sources.base import FatalSourceError, FetchContext, FetchResult, SourceAdapter, SourceQuota


class UnlockerAdapter(SourceAdapter):
    """API unlocker shown on the Bots tab. Collection runners use these during enrich."""

    group = "enrich"

    def fetch(self, ctx: FetchContext) -> FetchResult:
        raise FatalSourceError(
            f"{self.label} is a contact unlocker, not a lead discovery bot. "
            "Start a Runner; unlockers are used when fetching public pages."
        )


class ScrapingBeeAdapter(UnlockerAdapter):
    key = "scrapingbee"
    label = "ScrapingBee"
    description = (
        "HTML fetch proxy for public business pages when direct HTTP is blocked. "
        "Free plan ~1,000 credits. Used during contact extraction, not lead discovery."
    )
    quota = SourceQuota(
        requests=50,
        period="day",
        timezone="Europe/London",
        title="50 fetches / day",
        detail="ScrapingBee free credits are lifetime (~1,000). Leadlane caps daily use.",
    )

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        if not (settings.scrapingbee_api_key or "").strip():
            return False, "Add SCRAPINGBEE_API_KEY to .env (app.scrapingbee.com)."
        return True, ""


class ApifyAdapter(UnlockerAdapter):
    key = "apify"
    label = "Apify"
    description = (
        "Optional actor enrichment and/or cloud host. Needs APIFY_TOKEN "
        "(and APIFY_ACTOR_ID for actor runs). Free plan includes monthly compute credit."
    )
    quota = SourceQuota(
        requests=20,
        period="day",
        timezone="Europe/London",
        title="20 actor calls / day",
        detail="Apify free plan ~$5 compute / month. Leadlane caps actor calls lightly.",
    )

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        if not (settings.apify_token or "").strip():
            return False, "Add APIFY_TOKEN to .env (console.apify.com)."
        return True, ""
