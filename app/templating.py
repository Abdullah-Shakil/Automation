from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.sources.registry import default_registry
from app.trades import list_presets, preset_label, snapshot_for_preset


APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

STATUS_LABELS = {
    "idle": "Idle",
    "active": "Active",
    "running": "Running",
    "paused": "Paused",
    "limit_reached": "Limit reached",
    "stopped": "Stopped",
    "completed": "Completed",
    "error": "Error",
}


def format_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    zone = ZoneInfo(get_settings().usage_timezone)
    return value.astimezone(zone).strftime("%d %b %Y %H:%M")


def format_remaining(value: datetime | None, *, now: datetime | None = None) -> str:
    """Human time until value (usage window reset)."""
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    secs = int((value - clock).total_seconds())
    if secs <= 0:
        return "now"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours > 0:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes > 0:
        return f"{minutes}m"
    return "<1m"


def profession_list(bot) -> str:
    preset = getattr(bot, "trade_preset", None) or ""
    if preset:
        return preset_label(preset)
    # Fallback: describe from current Find snapshot (bots no longer store professions)
    labels = [row["label"] for row in snapshot_for_preset("")]
    return ", ".join(labels)


def source_label(key: str) -> str:
    try:
        return default_registry.get(key).label
    except KeyError:
        return key


def success_rate(bot) -> str:
    done = (bot.steps_succeeded or 0) + (bot.steps_failed or 0)
    if done == 0:
        return "—"
    return f"{round(100 * bot.steps_succeeded / done)}%"


def failure_rate(bot) -> str:
    done = (bot.steps_succeeded or 0) + (bot.steps_failed or 0)
    if done == 0:
        return "—"
    return f"{round(100 * bot.steps_failed / done)}%"


def leads_per_hour(bot) -> str:
    seconds = bot.run_seconds or 0
    if seconds <= 0 or bot.leads_found is None:
        return "—"
    return f"{bot.leads_found / (seconds / 3600):.1f}"


def format_duration(seconds: int | None) -> str:
    total = int(seconds or 0)
    if total < 60:
        return f"{total}s"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def describe_bot(bot) -> str:
    trades = profession_list(bot) or "the selected trades"
    try:
        adapter = default_registry.get(bot.source)
    except KeyError:
        return f"This bot collects {trades} across England."
    quota = adapter.quota
    return (
        f"This bot collects {trades} across England from {adapter.label}. "
        f"{adapter.description} Free quota: {quota.title}. {quota.detail}"
    )


templates.env.globals["format_dt"] = format_dt
templates.env.globals["format_remaining"] = format_remaining
templates.env.globals["status_label"] = lambda status: STATUS_LABELS.get(status, status)
templates.env.globals["profession_list"] = profession_list
templates.env.globals["source_label"] = source_label
templates.env.globals["success_rate"] = success_rate
templates.env.globals["failure_rate"] = failure_rate
templates.env.globals["leads_per_hour"] = leads_per_hour
templates.env.globals["format_duration"] = format_duration
templates.env.globals["describe_bot"] = describe_bot
templates.env.globals["TRADE_CHOICES"] = list_presets()
templates.env.globals["list_presets"] = list_presets
templates.env.globals["preset_label"] = preset_label
