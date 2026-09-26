import time
from dataclasses import dataclass

from app.config import Settings


class FatalSourceError(Exception):
    """A problem the user must fix. The job stops."""


class TransientSourceError(Exception):
    """A temporary problem. The job stays running and retries with backoff."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class SourceQuota:
    """Published free allowance for one source. Bots stop at this and resume when it resets."""

    requests: int
    period: str  # 5min | day | month
    timezone: str
    title: str
    detail: str

    def __post_init__(self) -> None:
        if self.period not in {"5min", "day", "month"}:
            raise ValueError(f"Unknown quota period: {self.period}")
        if self.requests < 0:
            raise ValueError("Quota cannot be negative.")


@dataclass
class RawLead:
    business_name: str
    profession: str
    address: str | None = None
    postcode: str | None = None
    phone: str | None = None
    website: str | None = None
    email: str | None = None
    description: str | None = None
    source: str = ""
    source_url: str | None = None
    external_id: str | None = None


@dataclass
class FetchResult:
    leads: list[RawLead]
    checkpoint: dict
    done: bool
    requests_made: int
    progress_note: str
    log_message: str


@dataclass
class FetchContext:
    location: str
    professions: list[dict]
    checkpoint: dict
    user_agent: str
    settings: Settings


class SourceAdapter:
    """One data source. Subclasses set key, label, description, group, and quota."""

    key: str = ""
    label: str = ""
    description: str = ""
    group: str = "collect"  # collect | unsupported
    quota: SourceQuota = SourceQuota(
        requests=1,
        period="day",
        timezone="Europe/London",
        title="1 request / day",
        detail="Replace this quota on the adapter.",
    )

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        return True, ""

    def estimated_requests(self, ctx: FetchContext) -> int:
        return 1

    def fetch(self, ctx: FetchContext) -> FetchResult:
        raise NotImplementedError


class Pacer:
    """Keeps a minimum gap between outbound calls in this process."""

    def __init__(self) -> None:
        self._last = 0.0

    def wait(self, seconds: float) -> None:
        if seconds <= 0 or self._last <= 0:
            self._last = time.monotonic()
            return
        delay = seconds - (time.monotonic() - self._last)
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()


def http_headers(user_agent: str) -> dict[str, str]:
    return {"User-Agent": user_agent, "Accept-Language": "en-GB"}
