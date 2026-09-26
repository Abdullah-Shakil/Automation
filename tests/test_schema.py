from sqlalchemy import inspect, select, text

from app.db import ensure_schema, get_engine, session_scope
from app.models import Bot, Worker


def test_ensure_schema_adds_missing_bot_columns():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS logs"))
        conn.execute(text("DROP TABLE IF EXISTS bots"))
        conn.execute(text("CREATE TABLE bots (id INTEGER PRIMARY KEY)"))

    ensure_schema(engine)
    columns = {col["name"] for col in inspect(engine).get_columns("bots")}
    assert {"key", "location", "status", "checkpoint", "created_at"} <= columns

    ensure_schema(engine)


def test_init_seed_creates_bots_and_workers():
    ensure_schema()
    with session_scope() as db:
        from app.seed import seed

        seed(db)
        assert db.scalars(select(Bot).where(Bot.key == "overpass")).first() is not None
        assert db.scalars(select(Worker).where(Worker.key == "serper")).first() is not None
