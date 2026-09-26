from sqlalchemy import func, or_, select

from app.db import init_db, session_scope
from app.models import Lead, Log

init_db()
with session_scope() as db:
    cols = [c.name for c in Lead.__table__.columns]
    print("cols", cols)
    n = db.scalar(select(func.count()).select_from(Lead))
    with_web = db.scalar(select(func.count()).select_from(Lead).where(Lead.website.isnot(None), Lead.website != ""))
    with_email = db.scalar(select(func.count()).select_from(Lead).where(Lead.email.isnot(None), Lead.email != ""))
    with_phone = db.scalar(select(func.count()).select_from(Lead).where(Lead.phone.isnot(None), Lead.phone != ""))
    with_mobile = db.scalar(select(func.count()).select_from(Lead).where(Lead.mobile.isnot(None), Lead.mobile != ""))
    enriched = db.scalar(select(func.count()).select_from(Lead).where(Lead.enriched_at.isnot(None)))
    print("n", n, "web", with_web, "email", with_email, "phone", with_phone, "mobile", with_mobile, "enriched_at", enriched)
    for L in db.scalars(select(Lead).order_by(Lead.id.desc()).limit(8)).all():
        print(
            {
                "name": L.company_name,
                "src": L.primary_source,
                "web": L.website,
                "email": L.email,
                "phone": L.phone,
                "mobile": L.mobile,
                "enriched_at": L.enriched_at,
            }
        )
    for e in db.scalars(select(Log).order_by(Log.id.desc()).limit(25)).all():
        msg = e.message or ""
        if "enrich" in msg.lower() or "website" in msg.lower() or e.kind == "enrich":
            print("LOG", e.created_at, e.level, e.kind, e.ref_key, msg[:140])
