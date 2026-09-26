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
from app.sources.registry import default_registry
from app.templating import templates

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
    row = db.get(WorkerHeartbeat, 1)
    if row is None:
        return {
            "online": False,
            "last_seen": None,
            "detail": "The background worker has not reported in yet. On this PC that is python -m app.worker. With the PC off, the scheduled GitHub Actions run does the work.",
        }
    last = as_utc(row.last_seen)
    stale_after = max(30.0, settings.worker_poll_seconds * 4)
    online = last is not None and (now - last).total_seconds() <= stale_after
    if online:
        detail = "A worker reported in just now. With the cloud database, the GitHub Actions schedule keeps going after you close this PC."
    else:
        detail = "The worker looks stopped. Bots continue on the next scheduled GitHub Actions run, or when python -m app.worker is running on this PC."
    return {"online": online, "last_seen": last, "detail": detail}


def _source_views(settings: Settings) -> tuple[list[dict], list[dict]]:
    collect = []
    unsupported = []
    for adapter in default_registry.all():
        available, reason = adapter.is_available(settings)
        item = {
            "key": adapter.key,
            "label": adapter.label,
            "description": adapter.description,
            "quota_title": adapter.quota.title,
            "available": available,
            "reason": reason,
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


def _quota_rows(db, now: datetime) -> list[dict]:
    rows = []
    for adapter in default_registry.all():
        if adapter.group != "collect":
            continue
        quota = adapter.quota
        window = ensure_window(db, adapter.key, now, quota)
        _start, reset_at = window_bounds(now, quota)
        rows.append(
            {
                "key": adapter.key,
                "label": adapter.label,
                "title": quota.title,
                "detail": quota.detail,
                "used": window.requests_used,
                "limit": quota.requests,
                "reset_at": reset_at,
                "period": period_label(quota.period),
            }
        )
    return rows


def _live_context(db, settings: Settings) -> dict:
    now = _now()
    bots = db.scalars(select(Bot).order_by(Bot.updated_at.desc(), Bot.id.desc())).all()
    logs = db.scalars(select(BotLog).order_by(BotLog.id.desc()).limit(12)).all()
    return {
        "bots": bots,
        "logs": logs,
        "quotas": _quota_rows(db, now),
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
    if page and page > 1:
        pairs.append(("page", str(page)))
    return urlencode(pairs)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    settings = get_settings()
    with session_scope() as db:
        live = _live_context(db, settings)
        professions = db.scalars(select(Profession).order_by(Profession.label.asc())).all()
        sources, unsupported = _source_views(settings)
        return _render(
            request,
            "dashboard.html",
            {
                **live,
                "professions": professions,
                "sources": sources,
                "unsupported": unsupported,
                "active": "dashboard",
            },
        )


@router.get("/partials/dashboard", response_class=HTMLResponse)
def dashboard_partial(request: Request):
    settings = get_settings()
    with session_scope() as db:
        return _render(
            request,
            "_live.html",
            {**_live_context(db, settings), "active": "dashboard"},
        )


@router.post("/bots")
async def create_bot(request: Request):
    form = await _form(request)
    location = " ".join(str(form.get("location") or "").split())
    source = str(form.get("source") or "")
    slugs = [str(item) for item in form.getlist("professions")]
    if len(location) < 2 or len(location) > 120:
        return _bad_form(request, "/", "Enter a town, city, or postcode.")
    if any(ord(char) < 32 for char in location):
        return _bad_form(request, "/", "That location contains characters we can't use.")
    settings = get_settings()
    try:
        adapter = default_registry.get(source)
    except KeyError:
        return _bad_form(request, "/", "Choose a data source.")
    available, reason = adapter.is_available(settings)
    if adapter.group == "unsupported" or not available:
        return _bad_form(request, "/", reason or "That source is not available.")
    if not slugs:
        return _bad_form(request, "/", "Choose at least one profession.")
    now = _now()
    with session_scope() as db:
        rows = db.scalars(select(Profession).where(Profession.slug.in_(slugs))).all()
        by_slug = {row.slug: row for row in rows}
        missing = [slug for slug in slugs if slug not in by_slug]
        if missing:
            return _bad_form(request, "/", "One of those professions is no longer in the list.")
        snapshot = [_profession_snapshot(by_slug[slug]) for slug in slugs]
        bot = Bot(
            location=location,
            source=source,
            status="running",
            professions=snapshot,
            checkpoint={},
            progress_note="Waiting for the worker.",
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
        from app.services.runner import add_log

        add_log(
            db,
            bot,
            "info",
            "Bot added. Collection continues in the cloud worker if this PC is off.",
            now,
        )
        return _go(f"/bots/{bot.id}", "Bot added.")


def _profession_snapshot(row: Profession) -> dict:
    return {
        "slug": row.slug,
        "label": row.label,
        "keywords": list(row.keywords or []),
        "sic_codes": list(row.sic_codes or []),
        "osm_tags": list(row.osm_tags or []),
    }


@router.post("/bots/{bot_id}/start")
async def start_bot(request: Request, bot_id: int):
    return await _set_bot_status(request, bot_id, "running")


@router.post("/bots/{bot_id}/pause")
async def pause_bot(request: Request, bot_id: int):
    return await _set_bot_status(request, bot_id, "paused")


@router.post("/bots/{bot_id}/stop")
async def stop_bot(request: Request, bot_id: int):
    return await _set_bot_status(request, bot_id, "stopped")


async def _set_bot_status(request: Request, bot_id: int, status: str):
    fallback = f"/bots/{bot_id}"
    now = _now()
    with session_scope() as db:
        bot = db.get(Bot, bot_id)
        if bot is None:
            return _go("/", "That bot no longer exists.")
        from app.services.runner import add_log

        if status == "running":
            try:
                adapter = default_registry.get(bot.source)
            except KeyError:
                return _bad_form(request, fallback, "That data source is no longer available.")
            window = ensure_window(db, bot.source, now, adapter.quota)
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
                add_log(db, bot, "info", "Started. The worker will carry on from the saved position.", now)
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
