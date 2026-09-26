from app.config import Settings
from app.sources.base import FatalSourceError, FetchContext, FetchResult, SourceAdapter, SourceQuota

# These platforms either have no official API for searching local businesses to
# export as sales leads, or their terms prohibit scraping (including logged-in
# pages and unofficial clients). They are registered so the gap is visible, and
# so a future official integration has an obvious place to land. They never fetch.


class UnsupportedSource(SourceAdapter):
    def __init__(self, key: str, label: str, reason: str):
        self.key = key
        self.label = label
        self.description = reason
        self.group = "unsupported"
        self._reason = reason
        self.quota = SourceQuota(
            requests=0,
            period="day",
            timezone="Europe/London",
            title="No free API",
            detail="There is no official free API for searching these listings. This source cannot run.",
        )

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        return False, self._reason

    def fetch(self, ctx: FetchContext) -> FetchResult:
        raise FatalSourceError(self._reason)


def social_stubs() -> list[UnsupportedSource]:
    return [
        UnsupportedSource(
            "facebook",
            "Facebook",
            "Meta does not offer a public API for searching local businesses to export as leads. "
            "Scraping Facebook, including behind a login, breaks Meta's terms. Left unimplemented.",
        ),
        UnsupportedSource(
            "instagram",
            "Instagram",
            "Instagram has no official local-business search for this use. "
            "Scraping the site or the private app API breaks Meta's terms. Left unimplemented.",
        ),
        UnsupportedSource(
            "linkedin",
            "LinkedIn",
            "LinkedIn's user agreement prohibits scraping. The official APIs do not provide "
            "a search of local trades for cold outreach. Left unimplemented.",
        ),
        UnsupportedSource(
            "tiktok",
            "TikTok",
            "TikTok does not offer an official API for collecting local business contacts. "
            "Scraping the site breaks TikTok's terms. Left unimplemented.",
        ),
        UnsupportedSource(
            "nextdoor",
            "Nextdoor",
            "Nextdoor does not publish an API for exporting neighbourhood businesses as leads. "
            "Scraping it breaks Nextdoor's terms. Left unimplemented.",
        ),
    ]
