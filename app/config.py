from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    database_url: str = "sqlite:///./data/leadlane.db"

    usage_timezone: str = "Europe/London"
    worker_poll_seconds: float = Field(default=3, gt=0)
    max_consecutive_errors: int = Field(default=5, ge=1)

    nominatim_min_interval_seconds: float = 1.1
    overpass_min_interval_seconds: float = 2
    companies_house_min_interval_seconds: float = 0.6
    directory_min_interval_seconds: float = 2

    user_agent: str = "Leadlane/1.0 (UK small-business research)"

    companies_house_api_key: str = ""
    google_places_api_key: str = ""
    google_places_enabled: bool = False
    directory_search_url_template: str = ""
    directory_name: str = "Business directory"
    wikidata_sparql_url: str = "https://query.wikidata.org/sparql"
    wikidata_min_interval_seconds: float = 1.2

    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    companies_house_api_base: str = "https://api.company-information.service.gov.uk"

    @field_validator("usage_timezone")
    @classmethod
    def _timezone_exists(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator(
        "companies_house_api_key",
        "google_places_api_key",
        "directory_search_url_template",
        "wikidata_sparql_url",
        "user_agent",
    )
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


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
