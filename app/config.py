from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def build_supabase_database_url(
    supabase_url: str,
    password: str,
    *,
    region: str = "",
    pooler_host: str = "",
) -> str:
    """Build a Postgres URI for Supabase.

    Free-tier direct hosts are often IPv6-only, so this prefers the IPv4 pooler
    when a region (or full pooler host) is set. Session mode (port 5432) suits
    a long-lived dashboard/worker better than transaction mode.
    """
    host = (urlparse(supabase_url.strip()).hostname or "").strip()
    ref = host.split(".")[0] if host else ""
    if not ref or not password.strip():
        raise ValueError("Supabase URL and database password are required to build DATABASE_URL.")
    user = f"postgres.{ref}"
    secret = quote_plus(password.strip())
    region = (region or "").strip()
    pooler_host = (pooler_host or "").strip()
    if pooler_host:
        return f"postgresql://{user}:{secret}@{pooler_host}:5432/postgres?sslmode=require"
    if region:
        return (
            f"postgresql://{user}:{secret}"
            f"@aws-0-{region}.pooler.supabase.com:5432/postgres?sslmode=require"
        )
    # Last resort: direct DB host (needs working IPv6 on many free projects).
    return f"postgresql://postgres:{secret}@db.{ref}.supabase.co:5432/postgres?sslmode=require"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    database_url: str = "sqlite:///./data/leadlane.db"

    # Optional: when set and DATABASE_URL is still sqlite, Postgres on Supabase is used instead.
    supabase_url: str = ""
    supabase_db_password: str = ""
    supabase_service_role_key: str = ""
    # Project region for the pooler, e.g. eu-central-1 (Settings -> Database -> Connection string).
    supabase_db_region: str = ""
    # Optional full host override, e.g. aws-0-eu-central-1.pooler.supabase.com
    supabase_db_pooler_host: str = ""

    usage_timezone: str = "Europe/London"
    worker_poll_seconds: float = Field(default=3, gt=0)
    max_consecutive_errors: int = Field(default=5, ge=1)

    nominatim_min_interval_seconds: float = 1.1
    overpass_min_interval_seconds: float = 2
    companies_house_min_interval_seconds: float = 0.6
    serper_min_interval_seconds: float = 1.0
    tavily_min_interval_seconds: float = 1.0
    serpapi_min_interval_seconds: float = 1.5

    user_agent: str = "Leadlane/1.0 (UK small-business research)"

    companies_house_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    serper_api_key: str = ""
    tavily_api_key: str = ""
    serpapi_api_key: str = ""
    google_places_api_key: str = ""
    scrapingbee_api_key: str = ""
    apify_token: str = ""
    apify_actor_id: str = ""
    # Mirrors of cloud schedulers. Empty means that scheduler is off.
    # The dashboard never collects. GitHub Actions is the process that does.
    leadlane_github_schedule: str = ""
    leadlane_cronjob_org: str = ""
    leadlane_cloudflare_cron: str = ""
    leadlane_vercel_cron: str = ""
    leadlane_gcp_cron: str = ""
    leadlane_render: str = ""
    leadlane_apify_host: str = ""
    wikidata_sparql_url: str = "https://query.wikidata.org/sparql"
    wikidata_min_interval_seconds: float = 1.2

    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    companies_house_api_base: str = "https://api.company-information.service.gov.uk"
    google_places_min_interval_seconds: float = 1.0

    @field_validator("usage_timezone")
    @classmethod
    def _timezone_exists(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator(
        "companies_house_api_key",
        "gemini_api_key",
        "groq_api_key",
        "serper_api_key",
        "tavily_api_key",
        "serpapi_api_key",
        "google_places_api_key",
        "scrapingbee_api_key",
        "apify_token",
        "apify_actor_id",
        "leadlane_github_schedule",
        "leadlane_cronjob_org",
        "leadlane_cloudflare_cron",
        "leadlane_vercel_cron",
        "leadlane_gcp_cron",
        "leadlane_render",
        "leadlane_apify_host",
        "wikidata_sparql_url",
        "user_agent",
        "supabase_url",
        "supabase_db_password",
        "supabase_service_role_key",
        "supabase_db_region",
        "supabase_db_pooler_host",
    )
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _resolve_supabase_database(self):
        url = (self.database_url or "").strip()
        if url.startswith(("postgres://", "postgresql://")):
            return self
        if self.supabase_url and self.supabase_db_password:
            object.__setattr__(
                self,
                "database_url",
                build_supabase_database_url(
                    self.supabase_url,
                    self.supabase_db_password,
                    region=self.supabase_db_region,
                    pooler_host=self.supabase_db_pooler_host,
                ),
            )
        return self


def normalize_database_url(url: str) -> str:
    """Neon, Supabase, and others issue postgres:// URLs. SQLAlchemy wants an explicit driver."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
