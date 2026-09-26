"""Free online workers the owner connects with an API key from a signup page."""

from dataclasses import dataclass

from app.config import Settings
from app.sources.base import SourceQuota


@dataclass(frozen=True)
class FreeWorker:
    """A third-party free-tier service shown on the Workers tab."""

    key: str
    label: str
    description: str
    signup_url: str
    signup_label: str
    env_name: str
    quota: SourceQuota
    can_collect: bool
    """True when this worker is also a collect source (bots can run it)."""

    def key_value(self, settings: Settings) -> str:
        return (getattr(settings, self.env_name.lower(), None) or "").strip()

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        if self.key_value(settings):
            return True, ""
        return (
            False,
            f"Add {self.env_name} to .env after you create a key at {self.signup_url}, then restart the dashboard.",
        )
