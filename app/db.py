from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Boolean, Integer, JSON, String, Text, UniqueConstraint, create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateColumn

from app.config import get_settings, normalize_database_url
from app.models import Base

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def _sqlite_path(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    raw = url[len("sqlite:///") :]
    if raw == ":memory:" or raw.startswith("file:"):
        return None
    return Path(raw)


def dispose_engine() -> None:
    """Drop the cached engine so the next call rebuilds from current settings."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


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


def _quote_ident(name: str, dialect) -> str:
    return dialect.identifier_preparer.quote(name)


def _fallback_default_sql(column, dialect_name: str) -> str | None:
    """Server default used only when ADD COLUMN needs NOT NULL on a live table."""
    if column.server_default is not None:
        return None
    py_default = column.default
    if py_default is not None and getattr(py_default, "is_scalar", False):
        value = py_default.arg
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            if dialect_name == "postgresql":
                return "true" if value else "false"
            return "1" if value else "0"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str):
            escaped = value.replace("'", "''")
            return f"'{escaped}'"
        if isinstance(value, dict):
            return "'{}'" if dialect_name.startswith("sqlite") else "'{}'::json"
        if isinstance(value, list):
            return "'[]'" if dialect_name.startswith("sqlite") else "'[]'::json"
    col_type = column.type
    if isinstance(col_type, Boolean):
        return "false" if dialect_name == "postgresql" else "0"
    if isinstance(col_type, Integer):
        return "0"
    if isinstance(col_type, (String, Text)):
        return "''"
    if isinstance(col_type, JSON):
        # professions/keywords are lists; checkpoint is a dict
        if column.name in {"checkpoint"}:
            return "'{}'" if dialect_name.startswith("sqlite") else "'{}'::json"
        return "'[]'" if dialect_name.startswith("sqlite") else "'[]'::json"
    return None


def _add_missing_column(engine: Engine, table_name: str, column) -> None:
    dialect = engine.dialect
    dialect_name = dialect.name
    col_sql = str(CreateColumn(column).compile(dialect=dialect)).strip()
    # CreateColumn may omit a DEFAULT for Python-side defaults; add one when NOT NULL.
    if (not column.nullable) and column.server_default is None and " DEFAULT " not in col_sql.upper():
        fallback = _fallback_default_sql(column, dialect_name)
        if fallback is not None:
            # Insert DEFAULT before trailing NULL/NOT NULL if present
            upper = col_sql.upper()
            for marker in (" NOT NULL", " NULL"):
                idx = upper.rfind(marker)
                if idx != -1:
                    col_sql = f"{col_sql[:idx]} DEFAULT {fallback}{col_sql[idx:]}"
                    break
            else:
                col_sql = f"{col_sql} DEFAULT {fallback}"
    stmt = text(
        f"ALTER TABLE {_quote_ident(table_name, dialect)} ADD COLUMN {col_sql}"
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def _ensure_indexes(engine: Engine, table) -> None:
    inspector = inspect(engine)
    existing = {idx["name"] for idx in inspector.get_indexes(table.name) if idx.get("name")}
    # Unique constraints often show up as unique indexes
    for uc in inspector.get_unique_constraints(table.name):
        if uc.get("name"):
            existing.add(uc["name"])
    for index in table.indexes:
        if not index.name or index.name in existing:
            continue
        try:
            index.create(bind=engine)
        except (OperationalError, ProgrammingError):
            # Concurrent create or already present under another name
            pass
    for constraint in table.constraints:
        if not isinstance(constraint, UniqueConstraint) or not constraint.name:
            continue
        if constraint.name in existing:
            continue
        cols = ", ".join(_quote_ident(col.name, engine.dialect) for col in constraint.columns)
        stmt = text(
            f"ALTER TABLE {_quote_ident(table.name, engine.dialect)} "
            f"ADD CONSTRAINT {_quote_ident(constraint.name, engine.dialect)} UNIQUE ({cols})"
        )
        try:
            with engine.begin() as conn:
                conn.execute(stmt)
        except (OperationalError, ProgrammingError):
            pass


def ensure_schema(engine: Engine | None = None) -> None:
    """Create missing tables, then add any missing columns and indexes.

    Safe to call on every startup. Does not drop or rename columns.
    """
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_cols = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_cols:
                continue
            try:
                _add_missing_column(engine, table.name, column)
            except (OperationalError, ProgrammingError):
                # Column may have been added by a concurrent process
                inspector = inspect(engine)
                refreshed = {col["name"] for col in inspector.get_columns(table.name)}
                if column.name not in refreshed:
                    raise
        _ensure_indexes(engine, table)


def init_db() -> None:
    from app.seed import seed

    get_engine()
    ensure_schema()
    with session_scope() as db:
        seed(db)


def reset_database() -> None:
    """Drop and recreate tables. Used by tests."""
    engine = get_engine()
    Base.metadata.drop_all(engine)
    dispose_engine()
    init_db()
