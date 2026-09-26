import logging
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.db import init_db
from app.routes import router
from app.templating import APP_DIR, templates

logger = logging.getLogger("leadlane")


def _nav_active(request: Request) -> str:
    path = request.url.path
    if path.startswith("/leads"):
        return "leads"
    if path.startswith("/bots"):
        return "bots"
    tab = (parse_qs(request.url.query).get("tab") or ["workers"])[0]
    if tab == "bots":
        return "bots"
    return "workers"


def _retry_href(request: Request) -> str:
    path = request.url.path
    if path.startswith("/partials/dashboard"):
        tab = (parse_qs(request.url.query).get("tab") or ["workers"])[0]
        return f"/?tab={tab}"
    if path.startswith("/partials/bots/"):
        key = path.rsplit("/", 1)[-1]
        return f"/bots/{key}"
    if path == "/":
        tab = (parse_qs(request.url.query).get("tab") or ["workers"])[0]
        return f"/?tab={tab}"
    return path or "/?tab=workers"


class CatchErrorsMiddleware(BaseHTTPMiddleware):
    """Keep the nav shell up; confine failures to the current page/tab body."""

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            logger.exception("Unhandled error on %s %s", request.method, request.url.path)
            active = _nav_active(request)
            retry = _retry_href(request)
            detail = str(exc)
            if request.url.path.startswith("/partials/"):
                return HTMLResponse(
                    (
                        '<section class="panel tab-error" role="alert">'
                        '<h2 class="section-title">Internal error</h2>'
                        '<p class="hint">This tab could not refresh. Other tabs should still work.</p>'
                        f'<p class="flash flash-error"><code>{detail}</code></p>'
                        f'<p class="hint"><a href="{retry}">Retry</a></p>'
                        "</section>"
                    ),
                    status_code=200,
                )
            try:
                return templates.TemplateResponse(
                    request,
                    "error.html",
                    {
                        "request": request,
                        "active": active,
                        "error_title": "Internal error",
                        "error_detail": detail,
                        "retry_href": retry,
                        "notice": None,
                        "error": None,
                    },
                    status_code=200,
                )
            except Exception:
                return HTMLResponse(
                    (
                        "<!DOCTYPE html><html><body>"
                        "<nav>"
                        '<a href="/?tab=workers">Workers</a> · '
                        '<a href="/?tab=bots">Bots</a> · '
                        '<a href="/leads">Leads</a>'
                        "</nav>"
                        f"<h1>Internal error</h1><p>{detail}</p>"
                        f'<p><a href="{retry}">Retry</a></p>'
                        "</body></html>"
                    ),
                    status_code=200,
                )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        init_db()
    except Exception:
        logger.exception("Database init failed — pages will show in-tab errors until DATABASE_URL works")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Leadlane", lifespan=lifespan)
    app.add_middleware(CatchErrorsMiddleware)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

    @app.middleware("http")
    async def guard(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/robots.txt")
    def robots():
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return RedirectResponse("/static/favicon.svg", status_code=302)

    return app


app = create_app()
