"""Dashboard routes — Workers start/stop, Bots are usage meters, Leads list."""

from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db import session_scope
from app.models import Bot, Lead, Log, Worker
from app.seed import ensure_bots, ensure_workers
from app.services.leads import LeadFilters, search_leads
from app.services.runner import as_utc, heartbeat_view, utcnow
from app.services.usage import ensure_window, period_label, window_bounds
from app.sources.registry import default_registry
from app.templating import format_remaining, templates
from app.profiles import merge_profile
from app.trades import list_presets, normalize_preset, preset_label
from app.workers.checks import cached_result, ensure_checks
from app.workers.registry import default_workers

router = APIRouter()


def _now():
    return utcnow()


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    context = dict(context)
    context["notice"] = request.query_params.get("notice")
    context["error"] = request.query_params.get("error")
    context["request"] = request
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _go(path: str, notice: str | None = None, error: str | None = None):
    parts = []
    if notice:
        parts.append(f"notice={quote(notice)}")
    if error:
        parts.append(f"error={quote(error)}")
    if parts:
        join = "&" if "?" in path else "?"
        path = f"{path}{join}{'&'.join(parts)}"
    return RedirectResponse(path, status_code=303)


def _bad(fallback: str, message: str):
    return _go(fallback, error=message)


def _tab_error_html(title: str, detail: str, retry_href: str = "/") -> str:
    safe_title = title.replace("<", "&lt;")
    safe_detail = str(detail).replace("<", "&lt;")
    safe_retry = retry_href.replace('"', "&quot;")
    return (
        '<section class="panel tab-error" role="alert">'
        f'<h2 class="section-title">{safe_title}</h2>'
        '<p class="hint">This tab could not load. Other tabs should still work — try Workers, Bots, or Leads above.</p>'
        f'<p class="flash flash-error"><code>{safe_detail}</code></p>'
        f'<p class="hint"><a href="{safe_retry}">Retry this tab</a></p>'
        "</section>"
    )


def _dashboard_shell(request: Request, tab: str, tab_error: str):
    """Keep nav + tab chrome; show the failure only inside the tab body."""
    return _render(
        request,
        "dashboard.html",
        {
            "tab": tab,
            "active": "bots" if tab == "bots" else "workers",
            "trade_label": "",
            "tab_error": tab_error,
        },
        status_code=200,
    )


def _tab_from_request(request: Request) -> str:
    tab = (request.query_params.get("tab") or "").strip().lower()
    if tab in {"workers", "bots"}:
        return tab
    return "workers"


def _ensure_rows(db) -> None:
    ensure_bots(db)
    ensure_workers(db)


def _source_views(settings: Settings, db, now) -> tuple[list[dict], list[dict]]:
    collect = []
    unsupported = []
    bots_by_key = {b.key: b for b in db.scalars(select(Bot).order_by(Bot.id.asc())).all()}
    for adapter in default_registry.all():
        available, reason = adapter.is_available(settings)
        quota = adapter.quota
        used = 0
        reset_at = window_bounds(now, quota)[1]
        bot = bots_by_key.get(adapter.key)
        if adapter.group == "collect" and bot is not None:
            window = ensure_window(db, adapter.key, now, quota)
            used = window.requests_used
            _start, reset_at = window_bounds(now, quota)
        item = {
            "key": adapter.key,
            "label": adapter.label,
            "description": adapter.description,
            "quota_title": quota.title,
            "quota_detail": quota.detail,
            "used": used,
            "limit": quota.requests,
            "reset_at": reset_at,
            "available": available,
            "reason": reason,
            "bot": bot,
        }
        if adapter.group == "unsupported":
            unsupported.append(item)
        else:
            collect.append(item)
    return collect, unsupported


def _worker_rows(settings: Settings, db, now) -> list[dict]:
    ensure_checks(settings)
    rows = []
    db_workers = {w.key: w for w in db.scalars(select(Worker).order_by(Worker.id.asc())).all()}
    for spec in default_workers.all():
        if not spec.can_collect:
            # Still show AI-assist keys as inactive info? User asked workers with start/stop for collect.
            # Keep non-collect out of the startable table; show on a small note instead.
            continue
        row = db_workers.get(spec.key)
        available, reason = spec.is_available(settings)
        check = cached_result(spec.key)
        if check is None:
            conn = "Not connected"
            conn_kind = "off"
            detail = reason or ""
            available = False
        elif check.ok:
            conn, conn_kind, detail = "Connected", "ok", ""
            available = True
        else:
            conn, conn_kind, detail = "Not connected", "fail", check.detail or reason or ""
            available = False
        quota = spec.quota
        used = 0
        reset_at = window_bounds(now, quota)[1]
        if row is not None:
            window = ensure_window(db, spec.key, now, quota)
            used = window.requests_used
            _s, reset_at = window_bounds(now, quota)
        rows.append(
            {
                "key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "signup_url": spec.signup_url,
                "signup_label": spec.signup_label,
                "env_name": spec.env_name,
                "quota_title": quota.title,
                "used": used,
                "limit": quota.requests,
                "reset_at": reset_at,
                "reset_in": format_remaining(reset_at, now=now),
                "available": available,
                "reason": detail,
                "status": row.status if row else "stopped",
                "conn": conn,
                "conn_kind": conn_kind,
                "row": row,
                "leads_found": int(row.leads_found or 0) if row else 0,
                "is_running": bool(row is not None and row.status in {"running", "limit_reached"}),
                "can_start": bool(available and row is not None and row.status not in {"running", "limit_reached"}),
            }
        )
    return rows


def _assist_workers(settings: Settings) -> list[dict]:
    ensure_checks(settings)
    rows = []
    for spec in default_workers.all():
        if spec.can_collect:
            continue
        check = cached_result(spec.key)
        if check is not None and check.ok:
            status, conn = "Connected", "Connected"
            reason = ""
        elif check is not None:
            status, conn = "Not connected", "Not connected"
            reason = check.detail or ""
        else:
            available, reason = spec.is_available(settings)
            status = "Connected" if available else "Not connected"
            conn = status
        rows.append(
            merge_profile(
                "worker",
                spec.key,
                {
                    "key": spec.key,
                    "label": spec.label,
                    "description": spec.description,
                    "env_name": spec.env_name,
                    "signup_url": spec.signup_url,
                    "signup_label": spec.signup_label,
                    "status": status,
                    "conn": conn,
                    "reason": reason,
                },
            )
        )
    return rows


def _profile_payload(sources: list[dict], workers: list[dict], cloud_runners: list[dict] | None = None) -> dict:
    bots = []
    for source in sources:
        bot = source.get("bot")
        if bot is None:
            continue
        pct = round(100 * source["used"] / max(source["limit"], 1), 1) if source["limit"] else None
        reset_at = source.get("reset_at")
        bots.append(
            merge_profile(
                "bot",
                bot.key,
                {
                    "id": f"bot-{bot.key}",
                    "key": bot.key,
                    "kind": "bot",
                    "name": bot.name or source["label"],
                    "description": source["description"],
                    "status": bot.status,
                    "status_label": bot.status.replace("_", " "),
                    "used": source["used"],
                    "limit": source["limit"],
                    "quota_title": source["quota_title"],
                    "quota_detail": source.get("quota_detail") or "",
                    "reset_at": reset_at.isoformat() if reset_at else None,
                    "reset_in": format_remaining(reset_at) if reset_at else None,
                    "pct_used": pct,
                    "leads_found": bot.leads_found,
                    "progress_note": bot.progress_note or "",
                    "last_error": bot.last_error or "",
                    "reason": source.get("reason") or "",
                    "connected": source.get("available"),
                    "conn": "Connected" if source.get("available") else "Not connected",
                    "profile_href": f"/profiles/bot/{bot.key}",
                },
            )
        )
    worker_profiles = []
    for w in workers:
        pct = round(100 * w["used"] / max(w["limit"], 1), 1) if w["limit"] else None
        reset_at = w.get("reset_at")
        worker_profiles.append(
            merge_profile(
                "worker",
                w["key"],
                {
                    "id": f"worker-{w['key']}",
                    "key": w["key"],
                    "kind": "worker",
                    "name": w["label"],
                    "description": w["description"],
                    "status": w["status"],
                    "status_label": w["status"].replace("_", " "),
                    "used": w["used"],
                    "limit": w["limit"],
                    "quota_title": w["quota_title"],
                    "reset_at": reset_at.isoformat() if reset_at else None,
                    "reset_in": w.get("reset_in") or (format_remaining(reset_at) if reset_at else None),
                    "pct_used": pct,
                    "env_name": w["env_name"],
                    "signup_url": w["signup_url"],
                    "signup_label": w["signup_label"],
                    "reason": w.get("reason") or "",
                    "leads_found": w.get("leads_found") or 0,
                    "connected": w.get("available"),
                    "conn": w.get("conn"),
                    "is_running": w.get("is_running"),
                    "progress_note": (w["row"].progress_note if w.get("row") else "") or "",
                    "last_error": (w["row"].last_error if w.get("row") else "") or "",
                    "profile_href": f"/profiles/worker/{w['key']}",
                },
            )
        )
    schedulers = []
    for runner in cloud_runners or []:
        schedulers.append(
            merge_profile(
                "scheduler",
                runner["key"],
                {
                    "id": f"scheduler-{runner['key']}",
                    "key": runner["key"],
                    "kind": "scheduler",
                    "name": runner["label"],
                    "description": runner.get("summary") or "",
                    "status": runner.get("status") or ("on" if runner.get("on") else "off"),
                    "status_label": runner.get("status") or ("On" if runner.get("on") else "Off"),
                    "conn": "Connected" if runner.get("on") else "Off",
                    "connected": bool(runner.get("on")),
                    "used": None,
                    "limit": None,
                    "quota_title": "Scheduler — no API quota",
                    "leads_found": None,
                    "selected": bool(runner.get("selected")),
                    "needs_card": bool(runner.get("needs_card")),
                    "setup": runner.get("setup") or "",
                    "profile_href": f"/profiles/scheduler/{runner['key']}",
                },
            )
        )
    return {"bots": bots, "workers": worker_profiles, "schedulers": schedulers}


def _dashboard_context(db, settings: Settings, tab: str) -> dict:
    from app.cloud.runners import get_trade_preset, runner_views, selected_runner
    from app.services.enrich import website_usage

    _ensure_rows(db)
    now = _now()
    sources, unsupported = _source_views(settings, db, now)
    workers = _worker_rows(settings, db, now)
    trade = get_trade_preset(db)
    cloud_runners = runner_views(settings, selected_runner(db))
    total_leads = int(db.scalar(select(func.count()).select_from(Lead)) or 0)
    logs = db.scalars(select(Log).order_by(Log.id.desc()).limit(20)).all()
    assist = _assist_workers(settings)
    profiles = _profile_payload(sources, workers, cloud_runners)
    for item in assist:
        key = item.get("key") or ""
        profiles.setdefault("workers", []).append(
            {
                **item,
                "id": f"worker-{key}",
                "kind": "worker",
                "name": item.get("label") or key,
                "status_label": item.get("status") or "",
                "leads_found": None,
            }
        )
    return {
        "tab": tab,
        "now": now,
        "trade_preset": trade,
        "trade_label": preset_label(trade),
        "trade_options": list_presets(),
        "sources": sources,
        "unsupported": unsupported,
        "workers": workers,
        "assist_workers": assist,
        "cloud_runners": cloud_runners,
        "total_leads": total_leads,
        "website_usage": website_usage(db, now),
        "worker": heartbeat_view(db, settings, now),
        "logs": logs,
        "profiles": profiles,
        "active": "bots" if tab == "bots" else "workers",
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    settings = get_settings()
    tab = _tab_from_request(request)
    try:
        with session_scope() as db:
            return _render(request, "dashboard.html", _dashboard_context(db, settings, tab))
    except Exception as exc:
        return _dashboard_shell(request, tab, f"Internal error loading {tab}: {exc}")


@router.get("/partials/dashboard", response_class=HTMLResponse)
def dashboard_partial(request: Request):
    settings = get_settings()
    tab = _tab_from_request(request)
    try:
        with session_scope() as db:
            return _render(request, "_dashboard_tab.html", _dashboard_context(db, settings, tab))
    except Exception as exc:
        return HTMLResponse(
            _tab_error_html("Internal error", f"Could not refresh the {tab} tab: {exc}", f"/?tab={tab}"),
            status_code=200,
        )


@router.post("/workers/{worker_key}/start")
async def start_worker(request: Request, worker_key: str):
    settings = get_settings()
    try:
        try:
            spec = default_workers.get(worker_key)
        except KeyError:
            return _bad("/?tab=workers", "Unknown worker.")
        if not spec.can_collect:
            return _bad("/?tab=workers", "That worker cannot collect leads.")
        available, reason = spec.is_available(settings)
        if not available:
            return _bad("/?tab=workers", reason or "Connect this worker in .env first.")
        now = _now()
        with session_scope() as db:
            _ensure_rows(db)
            row = db.scalars(select(Worker).where(Worker.key == worker_key)).first()
            if row is None:
                return _bad("/?tab=workers", "Worker row missing. Refresh and try again.")
            from app.services.runner import add_log

            if row.status == "completed":
                bot = db.scalars(select(Bot).where(Bot.key == worker_key)).first()
                if bot is not None:
                    bot.checkpoint = {}
                    bot.status = "idle"
            row.status = "running"
            row.last_error = ""
            row.progress_note = "Started. The cloud collector will use this worker on the next run."
            row.updated_at = now
            add_log(
                db,
                kind="worker",
                ref_key=worker_key,
                level="info",
                message="Worker started. Cloud / GitHub will cycle bots with remaining quota.",
                now=now,
            )
        return _go("/?tab=workers", f"{spec.label} started.")
    except Exception as exc:
        return _bad("/?tab=workers", f"Could not start worker: {exc}")


@router.post("/workers/{worker_key}/stop")
async def stop_worker(request: Request, worker_key: str):
    try:
        now = _now()
        with session_scope() as db:
            row = db.scalars(select(Worker).where(Worker.key == worker_key)).first()
            if row is None:
                return _bad("/?tab=workers", "That worker no longer exists.")
            from app.services.runner import add_log

            row.status = "stopped"
            row.progress_note = "Stopped. Progress on matching bots is saved."
            row.updated_at = now
            add_log(db, kind="worker", ref_key=worker_key, level="info", message="Worker stopped.", now=now)
            bot = db.scalars(select(Bot).where(Bot.key == worker_key)).first()
            if bot is not None and bot.status == "active":
                bot.status = "idle"
                bot.updated_at = now
        return _go("/?tab=workers", "Worker stopped.")
    except Exception as exc:
        return _bad("/?tab=workers", f"Could not stop worker: {exc}")


@router.post("/trade-preset")
async def choose_trade_preset(request: Request):
    try:
        from app.cloud.runners import set_trade_preset

        form = await request.form()
        preset = normalize_preset(str(form.get("trade_preset") or ""))
        with session_scope() as db:
            set_trade_preset(db, preset)
        return _go("/?tab=workers", f"Finding: {preset_label(preset)}.")
    except Exception as exc:
        return _bad("/?tab=workers", f"Could not save Find preset: {exc}")


@router.post("/cloud/runner")
async def choose_cloud_runner(request: Request):
    try:
        from app.cloud.runners import RUNNERS, runner_is_on, set_selected_runner

        form = await request.form()
        key = str(form.get("runner") or "").strip()
        match = next((item for item in RUNNERS if item.key == key), None)
        settings = get_settings()
        if match is None:
            return _bad("/?tab=workers", "That cloud runner is not available.")
        if match.needs_card:
            return _bad("/?tab=workers", f"{match.label} needs a card, so it stays off.")
        if not runner_is_on(settings, match):
            return _bad("/?tab=workers", f"{match.label} stays off until its switch is set.")
        with session_scope() as db:
            set_selected_runner(db, key)
        return _go("/?tab=workers", f"{match.label} selected as the scheduler label on this PC.")
    except Exception as exc:
        return _bad("/?tab=workers", f"Could not select cloud runner: {exc}")


@router.get("/leads", response_class=HTMLResponse)
def leads_page(request: Request):
    try:
        with session_scope() as db:
            rows, total = search_leads(db, LeadFilters(page=1, per_page=5000))
            return _render(request, "leads.html", {"leads": rows, "total": total, "active": "leads"})
    except Exception as exc:
        return _render(
            request,
            "leads.html",
            {"active": "leads", "leads": [], "total": 0, "tab_error": f"Internal error loading leads: {exc}"},
            status_code=200,
        )


@router.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_detail(request: Request, lead_id: str):
    if not lead_id.isdigit():
        return _render(
            request,
            "error.html",
            {
                "active": "leads",
                "error_title": "Not found",
                "error_detail": "That lead id is not valid.",
                "retry_href": "/leads",
            },
            status_code=404,
        )
    try:
        with session_scope() as db:
            lead = db.get(Lead, int(lead_id))
            if lead is None:
                return _go("/leads", error="That company is not in the database.")
            return _render(request, "lead.html", {"lead": lead, "active": "leads"})
    except Exception as exc:
        return _render(
            request,
            "error.html",
            {
                "active": "leads",
                "error_title": "Internal error",
                "error_detail": f"Lead detail failed: {exc}",
                "retry_href": "/leads",
            },
            status_code=200,
        )


@router.get("/bots/{bot_key}", response_class=HTMLResponse)
def bot_detail(request: Request, bot_key: str):
    settings = get_settings()
    try:
        with session_scope() as db:
            bot = db.scalars(select(Bot).where(Bot.key == bot_key)).first()
            if bot is None:
                return _go("/?tab=bots", error="That bot no longer exists.")
            return _render(request, "bot.html", _bot_context(db, settings, bot))
    except Exception as exc:
        return _render(
            request,
            "bot.html",
            {"active": "bots", "bot": None, "tab_error": f"Internal error loading bot: {exc}"},
            status_code=200,
        )


@router.get("/partials/bots/{bot_key}", response_class=HTMLResponse)
def bot_partial(request: Request, bot_key: str):
    settings = get_settings()
    try:
        with session_scope() as db:
            bot = db.scalars(select(Bot).where(Bot.key == bot_key)).first()
            if bot is None:
                return HTMLResponse(
                    _tab_error_html("Not found", "This bot no longer exists.", "/?tab=bots"),
                    status_code=200,
                )
            return _render(request, "_bot_live.html", _bot_context(db, settings, bot))
    except Exception as exc:
        return HTMLResponse(
            _tab_error_html("Internal error", f"Could not refresh this bot: {exc}", f"/bots/{bot_key}"),
            status_code=200,
        )


def _bot_context(db, settings: Settings, bot: Bot) -> dict:
    now = _now()
    logs = db.scalars(
        select(Log).where(Log.kind == "bot", Log.ref_key == bot.key).order_by(Log.id.desc()).limit(80)
    ).all()
    error_count = db.scalar(
        select(func.count())
        .select_from(Log)
        .where(Log.kind == "bot", Log.ref_key == bot.key, Log.level == "error")
    )
    quota_row = None
    try:
        adapter = default_registry.get(bot.key)
    except KeyError:
        adapter = None
    if adapter is not None:
        window = ensure_window(db, bot.key, now, adapter.quota)
        _start, reset_at = window_bounds(now, adapter.quota)
        quota_row = {
            "label": adapter.label,
            "title": adapter.quota.title,
            "detail": adapter.quota.detail,
            "used": window.requests_used,
            "limit": adapter.quota.requests,
            "reset_at": reset_at,
            "period": period_label(adapter.quota.period),
        }
    return {
        "bot": bot,
        "logs": logs,
        "error_count": int(error_count or 0),
        "quota": quota_row,
        "worker": heartbeat_view(db, settings, now),
        "active": "bots",
    }


@router.get("/profiles/{kind}/{key}", response_class=HTMLResponse)
def profile_page(request: Request, kind: str, key: str):
    """Legacy URL → same-page overlay via ?profile= on the matching tab."""
    kind_norm = (kind or "").strip().lower()
    key_norm = (key or "").strip().lower()
    if kind_norm in {"bots", "bot"}:
        return _go(f"/?tab=bots&profile=bot-{key_norm}")
    if kind_norm in {"schedulers", "scheduler", "runners", "runner", "apis", "api"}:
        return _go(f"/?tab=workers&profile=scheduler-{key_norm}")
    return _go(f"/?tab=workers&profile=worker-{key_norm}")


@router.get("/professions")
@router.post("/professions")
@router.post("/professions/{slug}/delete")
@router.post("/professions/restore")
async def professions_gone(request: Request, slug: str | None = None):
    return _go("/?tab=workers", "Trades are fixed. Use Find on the Workers tab.")
