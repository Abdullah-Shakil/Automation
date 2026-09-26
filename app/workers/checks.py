"""Live connection checks for free workers. Results are cached so dashboard polling does not burn quota."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import httpx

from app.config import Settings, get_settings
from app.workers.registry import FreeWorker, default_workers

_CACHE_TTL_SECONDS = 600.0
_lock = threading.Lock()
_cache: dict[str, tuple[float, bool, str]] = {}


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    detail: str
    checked: bool


def clear_check_cache() -> None:
    with _lock:
        _cache.clear()


def cached_result(worker_key: str) -> CheckResult | None:
    with _lock:
        row = _cache.get(worker_key)
    if row is None:
        return None
    when, ok, detail = row
    if time.monotonic() - when > _CACHE_TTL_SECONDS:
        return None
    return CheckResult(ok=ok, detail=detail, checked=True)


def _store(worker_key: str, ok: bool, detail: str) -> CheckResult:
    with _lock:
        _cache[worker_key] = (time.monotonic(), ok, detail)
    return CheckResult(ok=ok, detail=detail, checked=True)


def verify_worker(worker: FreeWorker, settings: Settings) -> CheckResult:
    key = worker.key_value(settings)
    if not key:
        return _store(worker.key, False, f"Add {worker.env_name} to .env, then check again.")
    try:
        if worker.key == "gemini":
            return _check_gemini(key)
        if worker.key == "groq":
            return _check_groq(key)
        if worker.key == "serper":
            return _check_serper(key)
        if worker.key == "tavily":
            return _check_tavily(key)
        if worker.key == "serpapi":
            return _check_serpapi(key)
    except httpx.TimeoutException:
        return _store(worker.key, False, "Timed out reaching the API.")
    except httpx.HTTPError as exc:
        return _store(worker.key, False, f"Network error: {exc.__class__.__name__}")
    return _store(worker.key, False, "No check implemented for this worker.")


def verify_all(settings: Settings | None = None) -> dict[str, CheckResult]:
    get_settings.cache_clear()
    settings = settings or get_settings()
    clear_check_cache()
    return {worker.key: verify_worker(worker, settings) for worker in default_workers.all()}


def _check_gemini(api_key: str) -> CheckResult:
    response = httpx.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key, "pageSize": 1},
        timeout=20.0,
    )
    if response.status_code in {401, 403}:
        return _store("gemini", False, "Gemini rejected the API key.")
    if response.status_code >= 400:
        return _store("gemini", False, f"Gemini returned HTTP {response.status_code}.")
    return _store("gemini", True, "Gemini key works.")


def _check_groq(api_key: str) -> CheckResult:
    response = httpx.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=20.0,
    )
    if response.status_code in {401, 403}:
        return _store("groq", False, "Groq rejected the API key.")
    if response.status_code >= 400:
        return _store("groq", False, f"Groq returned HTTP {response.status_code}.")
    return _store("groq", True, "Groq key works.")


def _check_serper(api_key: str) -> CheckResult:
    response = httpx.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": "plumbers UK", "gl": "uk", "hl": "en", "num": 1},
        timeout=20.0,
    )
    if response.status_code in {401, 403}:
        return _store("serper", False, "Serper rejected the API key.")
    if response.status_code == 429:
        return _store("serper", True, "Serper key accepted (rate limited just now).")
    if response.status_code >= 400:
        return _store("serper", False, f"Serper returned HTTP {response.status_code}.")
    return _store("serper", True, "Serper key works.")


def _check_tavily(api_key: str) -> CheckResult:
    response = httpx.post(
        "https://api.tavily.com/search",
        json={
            "api_key": api_key,
            "query": "plumbers United Kingdom",
            "search_depth": "basic",
            "max_results": 1,
            "include_answer": False,
        },
        timeout=25.0,
    )
    if response.status_code in {401, 403}:
        return _store("tavily", False, "Tavily rejected the API key.")
    if response.status_code == 429:
        return _store("tavily", True, "Tavily key accepted (rate limited just now).")
    if response.status_code >= 400:
        return _store("tavily", False, f"Tavily returned HTTP {response.status_code}.")
    return _store("tavily", True, "Tavily key works.")


def _check_serpapi(api_key: str) -> CheckResult:
    response = httpx.get(
        "https://serpapi.com/account.json",
        params={"api_key": api_key},
        timeout=20.0,
    )
    if response.status_code in {401, 403}:
        return _store("serpapi", False, "SerpApi rejected the API key.")
    if response.status_code >= 400:
        return _store("serpapi", False, f"SerpApi returned HTTP {response.status_code}.")
    return _store("serpapi", True, "SerpApi key works.")
