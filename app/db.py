import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings, insecure_defaults, normalize_database_url
from app.models import Base

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def _sqlite_path(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    raw = url[len("sqlite:///") :]
    if raw == ":memory:" or raw.startswith("file:"):
        return None
    return Path(raw)


def get_engine() -> Engine:
    global _engine, _Session
    if _engine is not None:
        return _engine
    url = normalize_database_url(get_settings().database_url)
    path = _sqlite_path(url)
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    _engine = engine
    _Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine


def get_sessionmaker() -> sessionmaker[Session]:
    get_engine()
    assert _Session is not None
    return _Session


@contextmanager
def session_scope() -> Iterator[Session]:
    maker = get_sessionmaker()
    session = maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    from app.seed import seed

    settings = get_settings()
    problems = insecure_defaults(settings)
    if problems and settings.environment.lower() != "production":
        logger.warning(
            "Using default %s. Fine on your own machine. Set ENVIRONMENT=production "
            "only after replacing them, or the app will refuse to start.",
            ", ".join(problems),
        )
    get_engine()
    Base.metadata.create_all(get_engine())
    with session_scope() as db:
        seed(db)


def reset_database() -> None:
    """Drop and recreate tables. Used by tests."""
    engine = get_engine()
    engine.dispose()
    Base.metadata.drop_all(engine)
    init_db()
