"""Inspect workers/bots/leads without printing secrets."""
from sqlalchemy import func, select

from app.config import get_settings
from app.db import init_db, session_scope
from app.models import Bot, Lead, Log, Worker

s = get_settings()
url = s.database_url
if "@" in url and "://" in url:
    pre, rest = url.split("://", 1)
    if "@" in rest:
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":")[0]
        url = f"{pre}://{user}:***@{host}"
print("db=", url[:140])
print("flags=", s.leadlane_github_schedule, s.leadlane_cronjob_org)

init_db()
with session_scope() as db:
    print("workers:")
    for w in db.scalars(select(Worker).order_by(Worker.key)).all():
        err = (w.last_error or "")[:100]
        print(f"  {w.key} status={w.status} leads={w.leads_found} err={err!r}")
    print("bots:")
    for b in db.scalars(select(Bot).order_by(Bot.key)).all():
        note = (b.progress_note or "")[:70]
        print(f"  {b.key} status={b.status} leads={b.leads_found} note={note!r}")
    print("lead_count=", db.scalar(select(func.count()).select_from(Lead)))
    print("recent_logs:")
    for e in db.scalars(select(Log).order_by(Log.id.desc()).limit(20)).all():
        print(f"  {e.created_at} {e.level} {e.kind}/{e.ref_key}: {(e.message or '')[:120]}")
