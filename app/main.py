import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import assert_production_config, get_settings
from app.db import init_db
from app.routes import router
from app.templating import APP_DIR

PUBLIC_PATHS = {"/login", "/health", "/robots.txt", "/favicon.ico"}
logger = logging.getLogger("leadlane")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    assert_production_config(settings)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Leadlane", lifespan=lifespan)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

    @app.middleware("http")
    async def guard(request, call_next):
        path = request.url.path
        public = path in PUBLIC_PATHS or path.startswith("/static/")
        if not public and not request.session.get("authenticated"):
            return RedirectResponse("/login", status_code=303)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if not path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="leadlane_session",
        same_site="lax",
        https_only=settings.environment.lower() == "production",
        max_age=60 * 60 * 24 * 14,
    )

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
