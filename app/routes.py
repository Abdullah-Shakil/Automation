from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db import session_scope
from app.models import Bot, BotLog, Lead, Profession, WorkerHeartbeat
from app.normalize import SIC_RE, TAG_RE, slugify
from app.seed import restore_builtins
from app.services.leads import LeadFilters, search_leads
from app.services.runner import as_utc, utcnow
from app.services.usage import ensure_window, has_capacity, period_label, window_bounds
from app.sources.base import SEARCH_LOCATION
from app.sources.registry import default_registry
from app.templating import templates
from app.workers.registry import default_workers

router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    context = dict(context)
    context["notice"] = request.query_params.get("notice")
    context["request"] = request
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _go(path: str, notice: str | None = None):
    if notice:
        join = "&" if "?" in path else "?"
        path = f"{path}{join}notice={quote(notice)}"
    return RedirectResponse(path, status_code=303)


def _bad_form(_request: Request, fallback: str, message: str):
    return _go(fallback, message)


async def _form(request: Request):
    return await request.form()


def _worker_view(db, settings: Settings, now: datetime) -> dict:
    """Heartbeat of the cloud collector. This PC never runs collection."""
    row = db.get(WorkerHeartbeat, 1)
    if row is None:
        return {
            "online": False,
            "last_seen": None,
            "detail": "The cloud collector has not reported in yet. This PC only stores start and stop. Collection runs on GitHub Actions after you turn a scheduler on.",
        }
    last = as_utc(row.last_seen)
    stale_after = max(30.0, settings.worker_poll_seconds * 4)
    online = last is not None and (now - last).total_seconds() <= stale_after
    if online:
        detail = "The cloud collector reported in just now. You can close this PC."
    else:
        detail = "The cloud collector is between runs. Bots stay as you left them and continue on the next cloud run."
    return {"online": online, "last_seen": last, "detail": detail}


def _profession_snapshot(row: Profession) -> dict:
    return {
        "slug": row.slug,
        "label": row.label,
        "keywords": list(row.keywords or []),
        "sic_codes": list(row.sic_codes or []),
        "osm_tags": list(row.osm_tags or []),
    }


def _ensure_worker_bots(db) -> None:
    """One bot per collectable source. Drops bots for sources that were removed."""
    from sqlalchemy import delete

    from app.services.runner import add_log

    profession_rows = db.scalars(select(Profession).order_by(Profession.label.asc())).all()
    snapshot = [_profession_snapshot(row) for row in profession_rows]
    now = _now()
    collect_keys = {adapter.key for adapter in default_registry.all() if adapter.group == "collect"}
    by_source: dict[str, Bot] = {}
    for bot in db.scalars(select(Bot).order_by(Bot.id.asc())).all():
        if bot.source not in collect_keys:
            db.execute(delete(BotLog).where(BotLog.bot_id == bot.id))
            db.delete(bot)
            continue
        if bot.source not in by_source:
            by_source[bot.source] = bot
    for adapter in default_registry.all():
        if adapter.group != "collect":
            continue
        bot = by_source.get(adapter.key)
        if bot is None:
            bot = Bot(
                name=adapter.label,
                location=SEARCH_LOCATION,
                source=adapter.key,
                status="stopped",
                professions=snapshot,
                checkpoint={},
                progress_note="Ready. Start when this source is connected.",
                selected_worker="",
                last_error="",
                leads_found=0,
                duplicates_found=0,
                requests_made=0,
                steps_succeeded=0,
                steps_failed=0,
                run_seconds=0,
                error_count=0,
                next_run_at=None,
                last_run_at=None,
                created_at=now,
                updated_at=now,
            )
            db.add(bot)
            db.flush()
            add_log(db, bot, "info", f"Bot created for source {adapter.label}.", now)
            by_source[adapter.key] = bot
        else:
            if not (bot.name or "").strip():
                bot.name = adapter.label
            bot.professions = snapshot
            bot.updated_at = now


def _source_views(settings: Settings, db=None, now: datetime | None = None) -> tuple[list[dict], list[dict]]:
    collect = []
    unsupported = []
    now = now or _now()
    bots_by_source: dict[str, Bot] = {}
    if db is not None:
        for bot in db.scalars(select(Bot).order_by(Bot.id.asc())).all():
            if bot.source not in bots_by_source:
                bots_by_source[bot.source] = bot
    for adapter in default_registry.all():
        available, reason = adapter.is_available(settings)
        quota = adapter.quota
        used = 0
        reset_at = window_bounds(now, quota)[1]
        if db is not None and adapter.group == "collect":
            window = ensure_window(db, adapter.key, now, quota)
            used = window.requests_used
            _start, reset_at = window_bounds(now, quota)
        bot = bots_by_source.get(adapter.key)
        effective_available = available
        effective_reason = reason
        if bot is not None and (bot.selected_worker or "").strip():
            try:
                override = default_registry.get(bot.selected_worker.strip())
                effective_available, effective_reason = override.is_available(settings)
            except KeyError:
                effective_available, effective_reason = False, "Selected worker is no longer available."
        can_start = bool(
            effective_available
            and bot is not None
            and bot.status not in {"running"}
        )
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
            "reason": reason if not (bot and bot.selected_worker) else effective_reason,
            "connection": "Connected" if available else "Not connected",
            "bot": bot,
            "can_start": can_start,
            "checked": False,
        }
        if adapter.group == "unsupported":
            unsupported.append(item)
        else:
            collect.append(item)
    for item in collect:
        if item["available"]:
            item["checked"] = True
            break
    return collect, unsupported


def _tab_from_request(request: Request) -> str:
    tab = (request.query_params.get("tab") or "").strip().lower()
    if tab in {"workers", "bots"}:
        return tab
    return "workers"


def _free_worker_views(settings: Settings) -> list[dict]:
    from app.workers.checks import cached_result

    rows = []
    for worker in default_workers.all():
        available, reason = worker.is_available(settings)
        check = cached_result(worker.key)
        if check is None:
            if available:
                status = "Key set"
                status_kind = "key"
                detail = f"Key is in .env. Click Check connections to verify with {worker.signup_label}."
            else:
                status = "Not connected"
                status_kind = "off"
                detail = reason
        elif check.ok:
            status = "Connected"
            status_kind = "ok"
            detail = check.detail
            available = True
        else:
            status = "Failed"
            status_kind = "fail"
            detail = check.detail
            available = False
        rows.append(
            {
                "key": worker.key,
                "label": worker.label,
                "description": worker.description,
                "signup_url": worker.signup_url,
                "signup_label": worker.signup_label,
                "env_name": worker.env_name,
                "quota_title": worker.quota.title,
                "available": available,
                "reason": detail,
                "status": status,
                "status_kind": status_kind,
                "can_collect": worker.can_collect,
                "connection": status,
            }
        )
    return rows


def _worker_choices(settings: Settings) -> list[dict]:
    """Connected free workers that can be activated on a bot."""
    from app.workers.checks import cached_result

    choices = []
    for worker in default_workers.all():
        available, _reason = worker.is_available(settings)
        check = cached_result(worker.key)
        if check is not None:
            available = check.ok
        elif not available:
            continue
        if not available:
            continue
        choices.append(
            {
                "key": worker.key,
                "label": worker.label,
                "can_collect": worker.can_collect,
            }
        )
    return choices


def _dashboard_context(db, settings: Settings, tab: str) -> dict:
    from app.cloud.runners import runner_views, selected_runner
    from app.services.enrich import website_usage

    _ensure_worker_bots(db)
    live = _live_context(db, settings)
    sources, unsupported = _source_views(settings, db, live["now"])
    professions = db.scalars(select(Profession).order_by(Profession.label.asc())).all()
    return {
        **live,
        "tab": tab,
        "professions": professions,
        "sources": sources,
        "unsupported": unsupported,
        "free_workers": _free_worker_views(settings),
        "worker_choices": _worker_choices(settings),
        "cloud_runners": runner_views(settings, selected_runner(db)),
        "website_usage": website_usage(db, live["now"]),
        "active": "dashboard",
    }


def _live_context(db, settings: Settings) -> dict:
    now = _now()
    bots = db.scalars(select(Bot).order_by(Bot.updated_at.desc(), Bot.id.desc())).all()
    logs = db.scalars(select(BotLog).order_by(BotLog.id.desc()).limit(12)).all()
    return {
        "bots": bots,
        "logs": logs,
        "worker": _worker_view(db, settings, now),
        "now": now,
    }


def _filters_from_query(request: Request, per_page: int = 50) -> LeadFilters:
    params = request.query_params
    try:
        page = int(params.get("page") or 1)
    except ValueError:
        page = 1
    return LeadFilters(
        q=(params.get("q") or "").strip(),
        profession=(params.get("profession") or "").strip(),
        source=(params.get("source") or "").strip(),
        has_email=params.get("has_email") == "1",
        has_phone=params.get("has_phone") == "1",
        has_mobile=params.get("has_mobile") == "1",
        company_number=(params.get("company_number") or "").strip(),
        town=(params.get("town") or "").strip(),
        status=(params.get("status") or "").strip(),
        sic=(params.get("sic") or "").strip(),
        page=page,
        per_page=per_page,
    )


def _filter_query(filters: LeadFilters, page: int | None = None) -> str:
    pairs = []
    if filters.q:
        pairs.append(("q", filters.q))
    if filters.profession:
        pairs.append(("profession", filters.profession))
    if filters.source:
        pairs.append(("source", filters.source))
    if filters.has_email:
        pairs.append(("has_email", "1"))
    if filters.has_phone:
        pairs.append(("has_phone", "1"))
    if filters.has_mobile:
        pairs.append(("has_mobile", "1"))
    if filters.company_number:
        pairs.append(("company_number", filters.company_number))
    if filters.town:
        pairs.append(("town", filters.town))
    if filters.status:
        pairs.append(("status", filters.status))
    if filters.sic:
        pairs.append(("sic", filters.sic))
    if page and page > 1:
        pairs.append(("page", str(page)))
    return urlencode(pairs)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    settings = get_settings()
    tab = _tab_from_request(request)
    with session_scope() as db:
        return _render(request, "dashboard.html", _dashboard_context(db, settings, tab))


@router.get("/partials/dashboard", response_class=HTMLResponse)
def dashboard_partial(request: Request):
    settings = get_settings()
    tab = _tab_from_request(request)
    with session_scope() as db:
        return _render(request, "_dashboard_tab.html", _dashboard_context(db, settings, tab))


@router.post("/workers/check")
def check_workers(request: Request):
    from app.workers.checks import verify_all

    results = verify_all()
    ok = sum(1 for item in results.values() if item.ok)
    total = len(results)
    return _go("/?tab=workers", f"Checked {total} workers: {ok} connected.")


@router.post("/bots/{bot_id}/start")
async def start_bot(request: Request, bot_id: int):
    return await _set_bot_status(request, bot_id, "running")


@router.post("/bots/{bot_id}/pause")
async def pause_bot(request: Request, bot_id: int):
    return await _set_bot_status(request, bot_id, "paused")


@router.post("/bots/{bot_id}/stop")
async def stop_bot(request: Request, bot_id: int):
    return await _set_bot_status(request, bot_id, "stopped")


@router.post("/bots/{bot_id}/worker")
async def assign_worker(request: Request, bot_id: int):
    tab = _tab_from_request(request)
    fallback = f"/?tab={tab}" if tab else f"/bots/{bot_id}"
    form = await request.form()
    selected = (form.get("selected_worker") or "").strip()
    settings = get_settings()
    if selected:
        try:
            worker = default_workers.get(selected)
        except KeyError:
            return _bad_form(request, fallback, "That worker is not available.")
        available, reason = worker.is_available(settings)
        if not available:
            return _bad_form(request, fallback, reason or "Connect that worker in .env first.")
        if not worker.can_collect:
            return _bad_form(
                request,
                fallback,
                f"{worker.label} is for AI assist only. Activate Serper, Tavily, or SerpApi to collect from search.",
            )
    now = _now()
    with session_scope() as db:
        bot = db.get(Bot, bot_id)
        if bot is None:
            return _go("/?tab=bots", "That bot no longer exists.")
        from app.services.runner import add_log

        bot.selected_worker = selected
        bot.updated_at = now
        if selected:
            add_log(db, bot, "info", f"Worker set to {default_workers.get(selected).label}.", now)
            notice = "Worker activated for this bot."
        else:
            add_log(db, bot, "info", "Worker selection cleared. The bot uses its own source.", now)
            notice = "Worker cleared."
    return _go(fallback, notice)


async def _set_bot_status(request: Request, bot_id: int, status: str):
    tab = _tab_from_request(request)
    fallback = f"/?tab={tab}" if tab else f"/bots/{bot_id}"
    now = _now()
    with session_scope() as db:
        bot = db.get(Bot, bot_id)
        if bot is None:
            return _go("/?tab=bots", "That bot no longer exists.")
        from app.services.runner import add_log

        if status == "running":
            settings = get_settings()
            source_key = (bot.selected_worker or "").strip() or bot.source
            try:
                adapter = default_registry.get(source_key)
            except KeyError:
                return _bad_form(request, fallback, "That source is no longer available.")
            available, reason = adapter.is_available(settings)
            if adapter.group == "unsupported" or not available:
                return _bad_form(request, fallback, reason or "Connect this source before starting.")
            window = ensure_window(db, source_key, now, adapter.quota)
            if bot.status == "completed":
                bot.checkpoint = {}
                bot.progress_note = "Started again from the beginning."
            bot.error_count = 0
            bot.last_error = ""
            bot.next_run_at = None
            if not has_capacity(window, adapter.quota, 1):
                bot.status = "limit_reached"
                add_log(db, bot, "warning", "Still at this source's free quota. It will resume when the quota resets.", now)
                notice = "The free quota is still used up. The bot stays paused until it resets."
            else:
                bot.status = "running"
                add_log(db, bot, "info", "Started. The cloud collector will carry on from the saved position.", now)
                notice = "Bot started."
        elif status == "paused":
            bot.status = "paused"
            add_log(db, bot, "info", "Paused. Start continues from here.", now)
            notice = "Bot paused."
        else:
            bot.status = "stopped"
            add_log(db, bot, "info", "Stopped. Progress is saved. Start continues from here.", now)
            notice = "Bot stopped. Progress is saved."
        bot.updated_at = now
    return _go(fallback, notice)


@router.get("/bots/{bot_id}", response_class=HTMLResponse)
def bot_detail(request: Request, bot_id: int):
    settings = get_settings()
    with session_scope() as db:
        bot = db.get(Bot, bot_id)
        if bot is None:
            return _go("/", "That bot no longer exists.")
        context = _bot_context(db, settings, bot)
        context["active"] = "dashboard"
        return _render(request, "bot.html", context)


@router.get("/partials/bots/{bot_id}", response_class=HTMLResponse)
def bot_partial(request: Request, bot_id: int):
    settings = get_settings()
    with session_scope() as db:
        bot = db.get(Bot, bot_id)
        if bot is None:
            return HTMLResponse("This bot no longer exists.", status_code=404)
        return _render(request, "_bot_live.html", _bot_context(db, settings, bot))


def _bot_context(db, settings: Settings, bot: Bot) -> dict:
    now = _now()
    logs = db.scalars(select(BotLog).where(BotLog.bot_id == bot.id).order_by(BotLog.id.desc()).limit(80)).all()
    error_count = db.scalar(
        select(func.count()).select_from(BotLog).where(BotLog.bot_id == bot.id, BotLog.level == "error")
    )
    quota_row = None
    try:
        adapter = default_registry.get(bot.source)
    except KeyError:
        adapter = None
    if adapter is not None:
        window = ensure_window(db, bot.source, now, adapter.quota)
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
        "worker": _worker_view(db, settings, now),
    }


@router.post("/cloud/runner")
async def choose_cloud_runner(request: Request):
    from app.cloud.runners import RUNNERS, runner_is_on, set_selected_runner

    form = await _form(request)
    key = str(form.get("runner") or "").strip()
    match = next((item for item in RUNNERS if item.key == key), None)
    settings = get_settings()
    if match is None:
        return _bad_form(request, "/?tab=workers", "That cloud runner is not available.")
    if match.needs_card:
        return _bad_form(request, "/?tab=workers", f"{match.label} needs a card, so it stays off.")
    if not runner_is_on(settings, match):
        return _bad_form(
            request,
            "/?tab=workers",
            f"{match.label} stays off until its switch is set. See the setup note on this page.",
        )
    with session_scope() as db:
        set_selected_runner(db, key)
    return _go("/?tab=workers", f"{match.label} is the scheduler this dashboard is showing. Collection still runs only in the cloud.")


@router.get("/leads", response_class=HTMLResponse)
def leads_page(request: Request):
    filters = _filters_from_query(request)
    with session_scope() as db:
        rows, total = search_leads(db, filters)
        pages = max(1, (total + filters.per_page - 1) // filters.per_page)
        page = min(max(filters.page, 1), pages)
        professions = db.scalars(select(Lead.profession).distinct().order_by(Lead.profession.asc())).all()
        sources = db.scalars(select(Lead.primary_source).distinct().order_by(Lead.primary_source.asc())).all()
        return _render(
            request,
            "leads.html",
            {
                "leads": rows,
                "total": total,
                "filters": filters,
                "page": page,
                "pages": pages,
                "professions": professions,
                "sources": sources,
                "query_base": _filter_query(filters),
                "active": "leads",
            },
        )


@router.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_detail(request: Request, lead_id: str):
    if not lead_id.isdigit():
        return HTMLResponse("Not found", status_code=404)
    with session_scope() as db:
        lead = db.get(Lead, int(lead_id))
        if lead is None:
            return _go("/leads", "That company is not in the database.")
        return _render(request, "lead.html", {"lead": lead, "active": "leads"})


@router.get("/professions", response_class=HTMLResponse)
def professions_page(request: Request):
    with session_scope() as db:
        rows = db.scalars(select(Profession).order_by(Profession.label.asc())).all()
        return _render(request, "professions.html", {"professions": rows, "active": "professions"})


@router.post("/professions")
async def add_profession(request: Request):
    form = await _form(request)
    label = " ".join(str(form.get("label") or "").split())
    if len(label) < 2 or len(label) > 80:
        return _bad_form(request, "/professions", "Enter a profession name.")
    try:
        keywords = _split_words(str(form.get("keywords") or ""), fallback=[label])
        sic_codes = _split_sic(str(form.get("sic_codes") or ""))
        osm_tags = _split_tags(str(form.get("osm_tags") or ""))
    except ValueError as exc:
        return _bad_form(request, "/professions", str(exc))
    if not keywords:
        return _bad_form(request, "/professions", "Add at least one search keyword.")
    slug = slugify(label)
    with session_scope() as db:
        taken = set(db.scalars(select(Profession.slug)).all())
        base = slug
        number = 2
        while slug in taken:
            slug = f"{base[:54]}-{number}"
            number += 1
        db.add(
            Profession(
                slug=slug,
                label=label,
                keywords=keywords,
                sic_codes=sic_codes,
                osm_tags=osm_tags,
                is_builtin=False,
                created_at=utcnow(),
            )
        )
    return _go("/professions", f"Added {label}.")


@router.post("/professions/{slug}/delete")
async def delete_profession(request: Request, slug: str):
    await _form(request)
    with session_scope() as db:
        row = db.scalar(select(Profession).where(Profession.slug == slug))
        if row is not None:
            db.delete(row)
    return _go("/professions", "Profession removed. Bots already running keep the copy they started with.")


@router.post("/professions/restore")
async def restore_professions(request: Request):
    await _form(request)
    with session_scope() as db:
        added = restore_builtins(db)
    return _go("/professions", f"Built-in list refreshed. {added} added back.")


def _split_words(text: str, fallback: list[str]) -> list[str]:
    parts = [" ".join(piece.split()) for piece in text.split(",")]
    parts = [piece for piece in parts if piece]
    if not parts:
        parts = fallback
    cleaned = []
    for piece in parts[:8]:
        if len(piece) > 40 or any(ord(char) < 32 for char in piece):
            raise ValueError("Keywords need to be short plain words, separated by commas.")
        cleaned.append(piece)
    return cleaned


def _split_sic(text: str) -> list[str]:
    codes = []
    for piece in text.split(","):
        code = piece.strip()
        if not code:
            continue
        if not SIC_RE.match(code):
            raise ValueError("SIC codes are 4 or 5 digits, separated by commas.")
        codes.append(code)
    return codes[:8]


def _split_tags(text: str) -> list[dict]:
    tags = []
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise ValueError("OpenStreetMap tags look like craft=plumber, separated by commas.")
        key, value = piece.split("=", 1)
        key, value = key.strip(), value.strip()
        if not TAG_RE.match(key) or not TAG_RE.match(value):
            raise ValueError("OpenStreetMap tags can only use letters, numbers, and : _ -.")
        tags.append({"key": key, "value": value})
    return tags[:8]
