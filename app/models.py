from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class Profession(Base):
    __tablename__ = "professions"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120))
    keywords: Mapped[list] = mapped_column(JSON)
    sic_codes: Mapped[list] = mapped_column(JSON)
    osm_tags: Mapped[list] = mapped_column(JSON)
    is_builtin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Bot(Base):
    """One collection the owner added: a profession set, an area, and a source."""

    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    location: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="running")
    professions: Mapped[list] = mapped_column(JSON)
    checkpoint: Mapped[dict] = mapped_column(JSON)
    progress_note: Mapped[str] = mapped_column(String(400), default="")
    last_error: Mapped[str] = mapped_column(String(500), default="")
    leads_found: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_found: Mapped[int] = mapped_column(Integer, default=0)
    requests_made: Mapped[int] = mapped_column(Integer, default=0)
    steps_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    steps_failed: Mapped[int] = mapped_column(Integer, default=0)
    run_seconds: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BotLog(Base):
    __tablename__ = "bot_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_name: Mapped[str] = mapped_column(String(300), index=True)
    profession: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    postcode: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    primary_source: Mapped[str] = mapped_column(String(40), index=True)
    primary_source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sources: Mapped[str] = mapped_column(String(240), default="")
    source_urls: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_found: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_bot_id: Mapped[int | None] = mapped_column(
        ForeignKey("bots.id", ondelete="SET NULL"), nullable=True
    )


class LeadIdentity(Base):
    __tablename__ = "lead_identities"
    __table_args__ = (UniqueConstraint("value", name="uq_lead_identity_value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    value: Mapped[str] = mapped_column(String(400))


class LeadSighting(Base):
    __tablename__ = "lead_sightings"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UsageWindow(Base):
    """Requests spent against one source's free quota in the current reset window."""

    __tablename__ = "usage_windows"
    __table_args__ = (UniqueConstraint("source", "window_key", name="uq_usage_source_window"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    window_key: Mapped[str] = mapped_column(String(80))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requests_used: Mapped[int] = mapped_column(Integer, default=0)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeat"

    id: Mapped[int] = mapped_column(primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pid: Mapped[int] = mapped_column(Integer)


