from sqlalchemy import inspect, text

from app.db import ensure_schema, get_engine, session_scope
from app.models import Profession
from sqlalchemy import select


def test_ensure_schema_adds_missing_bot_columns():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS bot_logs"))
        conn.execute(text("DROP TABLE IF EXISTS bots"))
        conn.execute(text("CREATE TABLE bots (id INTEGER PRIMARY KEY)"))

    ensure_schema(engine)
    columns = {col["name"] for col in inspect(engine).get_columns("bots")}
    assert {"location", "source", "status", "professions", "checkpoint", "created_at"} <= columns

    # Second pass is a no-op
    ensure_schema(engine)


def test_init_seed_survives_ensure_schema():
    ensure_schema()
    with session_scope() as db:
        assert db.scalars(select(Profession)).first() is not None
