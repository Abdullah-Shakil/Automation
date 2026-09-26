from app.sources.base import SourceQuota
from app.workers.base import FreeWorker

# Free tiers as published by each vendor (subject to change). Conservative caps.
_FREE_WORKERS: list[FreeWorker] = [
    FreeWorker(
        key="gemini",
        label="Google Gemini",
        description=(
            "Google AI Studio free tier. Create an API key after signing in. "
            "Useful for AI assist; lead discovery uses the search workers below so nothing is invented."
        ),
        signup_url="https://aistudio.google.com/apikey",
        signup_label="Google AI Studio",
        env_name="GEMINI_API_KEY",
        quota=SourceQuota(
            requests=250,
            period="day",
            timezone="Europe/London",
            title="Free tier (model limits)",
            detail="Rate limits depend on the model. Get a key free at Google AI Studio. Search grounding may need a paid Google AI plan.",
        ),
        can_collect=False,
    ),
    FreeWorker(
        key="groq",
        label="Groq",
        description=(
            "GroqCloud free API for fast open models. Create a key in the console after signing in. "
            "AI assist only — not used to invent business contacts."
        ),
        signup_url="https://console.groq.com/keys",
        signup_label="GroqCloud console",
        env_name="GROQ_API_KEY",
        quota=SourceQuota(
            requests=1000,
            period="day",
            timezone="America/Los_Angeles",
            title="Free tier (model RPM/RPD)",
            detail="Free daily limits vary by model in the Groq console. No card required for the free tier.",
        ),
        can_collect=False,
    ),
    FreeWorker(
        key="serper",
        label="Serper",
        description=(
            "Google search results API. Free trial queries on signup, no card required. "
            "Collects UK trade listings from organic search results (title, link, snippet)."
        ),
        signup_url="https://serper.dev/",
        signup_label="serper.dev",
        env_name="SERPER_API_KEY",
        quota=SourceQuota(
            requests=100,
            period="day",
            timezone="Europe/London",
            title="100 searches / day",
            detail="Vendor trial is about 2,500 queries once. Leadlane caps at 100 UK-day searches so a trial lasts longer.",
        ),
        can_collect=True,
    ),
    FreeWorker(
        key="tavily",
        label="Tavily",
        description=(
            "AI search API with a free monthly credit. Create a key in the dashboard after you register. "
            "Collects UK trade pages returned by Tavily search."
        ),
        signup_url="https://app.tavily.com/home",
        signup_label="Tavily dashboard",
        env_name="TAVILY_API_KEY",
        quota=SourceQuota(
            requests=50,
            period="day",
            timezone="Europe/London",
            title="50 searches / day",
            detail="Tavily's free plan is about 1,000 credits a month. Leadlane caps at 50 UK-day searches.",
        ),
        can_collect=True,
    ),
    FreeWorker(
        key="serpapi",
        label="SerpApi",
        description=(
            "Google SERP API with a small free monthly allowance. Sign up, copy the key from the dashboard. "
            "Collects UK trade listings from organic results."
        ),
        signup_url="https://serpapi.com/",
        signup_label="serpapi.com",
        env_name="SERPAPI_API_KEY",
        quota=SourceQuota(
            requests=20,
            period="day",
            timezone="Europe/London",
            title="20 searches / day",
            detail="SerpApi free plan is about 250 searches a month. Leadlane caps at 20 UK-day searches.",
        ),
        can_collect=True,
    ),
]


class WorkerRegistry:
    def __init__(self, items: list[FreeWorker] | None = None) -> None:
        self._items = {w.key: w for w in (items or list(_FREE_WORKERS))}

    def get(self, key: str) -> FreeWorker:
        try:
            return self._items[key]
        except KeyError as exc:
            raise KeyError(key) from exc

    def all(self) -> list[FreeWorker]:
        return list(self._items.values())

    def collect_keys(self) -> set[str]:
        return {w.key for w in self._items.values() if w.can_collect}


default_workers = WorkerRegistry()
