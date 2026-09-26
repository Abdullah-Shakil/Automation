from dataclasses import dataclass

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.models import Lead


@dataclass
class LeadFilters:
    q: str = ""
    profession: str = ""
    source: str = ""
    has_email: bool = False
    has_phone: bool = False
    has_mobile: bool = False
    company_number: str = ""
    town: str = ""
    status: str = ""
    sic: str = ""
    page: int = 1
    per_page: int = 50


def apply_filters(stmt: Select, filters: LeadFilters) -> Select:
    if filters.q:
        like = f"%{filters.q.strip()}%"
        stmt = stmt.where(
            or_(
                Lead.business_name.ilike(like),
                Lead.description.ilike(like),
                Lead.address.ilike(like),
                Lead.postcode.ilike(like),
                Lead.email.ilike(like),
                Lead.website.ilike(like),
                Lead.phone.ilike(like),
                Lead.mobile.ilike(like),
                Lead.trading_name.ilike(like),
                Lead.company_number.ilike(like),
                Lead.town.ilike(like),
                Lead.officers.ilike(like),
            )
        )
    if filters.company_number:
        stmt = stmt.where(Lead.company_number.ilike(f"%{filters.company_number.strip()}%"))
    if filters.town:
        stmt = stmt.where(Lead.town.ilike(f"%{filters.town.strip()}%"))
    if filters.status:
        stmt = stmt.where(Lead.company_status.ilike(filters.status.strip()))
    if filters.sic:
        stmt = stmt.where(Lead.sic_codes.ilike(f"%{filters.sic.strip()}%"))
    if filters.profession:
        stmt = stmt.where(Lead.profession == filters.profession)
    if filters.source:
        stmt = stmt.where(Lead.primary_source == filters.source)
    if filters.has_email:
        stmt = stmt.where(Lead.email.is_not(None), Lead.email != "")
    if filters.has_phone:
        stmt = stmt.where(
            or_(
                Lead.phone.is_not(None) & (Lead.phone != ""),
                Lead.mobile.is_not(None) & (Lead.mobile != ""),
            )
        )
    if filters.has_mobile:
        stmt = stmt.where(Lead.mobile.is_not(None), Lead.mobile != "")
    return stmt


def search_leads(db: Session, filters: LeadFilters) -> tuple[list[Lead], int]:
    count_stmt = apply_filters(select(func.count()).select_from(Lead), filters)
    total = int(db.scalar(count_stmt) or 0)
    page = max(filters.page, 1)
    stmt = (
        apply_filters(select(Lead), filters)
        .order_by(Lead.date_found.desc(), Lead.id.desc())
        .offset((page - 1) * filters.per_page)
        .limit(filters.per_page)
    )
    return list(db.scalars(stmt).all()), total
