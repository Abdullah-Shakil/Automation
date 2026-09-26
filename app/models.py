"""Minimal Leadlane schema — six tables after a clean Supabase reset."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class Bot(Base):
    """One collect source (Companies House, OSM, Serper, …). No user start/stop."""

    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    location: Mapped[str] = mapped_column(String(200), default="England")
    # idle | active | limit_reached | completed | error
    status: Mapped[str] = mapped_column(String(32), index=True, default="idle")
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
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


class Worker(Base):
    """Startable free collector (Serper / Tavily / SerpApi). Cloud only runs these when status=running."""

    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    # stopped | running | limit_reached | error
    status: Mapped[str] = mapped_column(String(32), index=True, default="stopped")
    progress_note: Mapped[str] = mapped_column(String(400), default="")
    last_error: Mapped[str] = mapped_column(String(500), default="")
    leads_found: Mapped[int] = mapped_column(Integer, default=0)
    requests_made: Mapped[int] = mapped_column(Integer, default=0)
    run_seconds: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Log(Base):
    """Shared activity / error log for bots and workers."""

    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # bot | worker
    ref_key: Mapped[str] = mapped_column(String(40), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Lead(Base):
    """One company row — contacts, address, sources, and dedupe keys in one place."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_name: Mapped[str] = mapped_column(String(300), index=True)
    profession: Mapped[str] = mapped_column(String(120), index=True, default="")
    category: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(40), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    town: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    county: Mapped[str | None] = mapped_column(String(120), nullable=True)
    postcode: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    trading_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    company_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    company_number: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    company_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sic_codes: Mapped[str | None] = mapped_column(String(240), nullable=True)
    incorporated_on: Mapped[str | None] = mapped_column(String(20), nullable=True)
    officers: Mapped[str | None] = mapped_column(Text, nullable=True)
    social_links: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_source: Mapped[str] = mapped_column(String(40), index=True, default="")
    primary_source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sources: Mapped[str] = mapped_column(String(240), default="")
    source_urls: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_keys: Mapped[list] = mapped_column(JSON, default=list)
    sightings: Mapped[list] = mapped_column(JSON, default=list)
    enrichment_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enrichment_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    date_found: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_bot_key: Mapped[str | None] = mapped_column(String(40), nullable=True)


class UsageWindow(Base):
    """Free-quota meter for one bot or worker key in the current reset window."""

    __tablename__ = "usage"
    __table_args__ = (UniqueConstraint("source", "window_key", name="uq_usage_source_window"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    window_key: Mapped[str] = mapped_column(String(80))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requests_used: Mapped[int] = mapped_column(Integer, default=0)


class AppSetting(Base):
    """Dashboard + cloud flags (trade_preset, cloud_runner, heartbeat)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), default="")
