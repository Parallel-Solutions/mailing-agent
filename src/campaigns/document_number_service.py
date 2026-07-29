"""Company-wide document number allocation per document type."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from src.infra.db import session_scope
from src.infra.models import Campaign, CompanyDocumentCounter, CompanyDocumentNumberAllocation, CompanyMembership


def resolve_campaign_company_id(campaign: Campaign) -> str:
    draft = dict(campaign.draft_payload or {})
    company_id = str(draft.get("company_id") or "").strip()
    if company_id:
        return company_id
    with session_scope() as session:
        membership = session.scalar(
            select(CompanyMembership)
            .where(CompanyMembership.username == campaign.owner_username)
            .order_by(CompanyMembership.created_at.asc())
            .limit(1)
        )
        if membership is None:
            return ""
        return str(membership.company_id or "").strip()


def build_allocation_key(*, campaign_id: str, recipient_id: int, template_id: str) -> str:
    return f"{campaign_id}:{recipient_id}:{template_id}"


def _next_counter_value(session, company_id: str, document_type_key: str) -> int:
    statement = (
        pg_insert(CompanyDocumentCounter)
        .values(company_id=company_id, document_type_key=document_type_key, last_number=1)
        .on_conflict_do_update(
            index_elements=[CompanyDocumentCounter.company_id, CompanyDocumentCounter.document_type_key],
            set_={
                "last_number": CompanyDocumentCounter.last_number + 1,
                "updated_at": func.now(),
            },
        )
        .returning(CompanyDocumentCounter.last_number)
    )
    return int(session.execute(statement).scalar_one())


def _get_allocated_number(session, *, company_id: str, document_type_key: str, allocation_key: str) -> int | None:
    value = session.scalar(
        select(CompanyDocumentNumberAllocation.number).where(
            CompanyDocumentNumberAllocation.company_id == company_id,
            CompanyDocumentNumberAllocation.document_type_key == document_type_key,
            CompanyDocumentNumberAllocation.allocation_key == allocation_key,
        )
    )
    return int(value) if value is not None else None


def peek_document_number(*, company_id: str, document_type_key: str) -> int:
    if not company_id:
        return 0
    with session_scope() as session:
        last_number = session.scalar(
            select(CompanyDocumentCounter.last_number).where(
                CompanyDocumentCounter.company_id == company_id,
                CompanyDocumentCounter.document_type_key == document_type_key,
            )
        )
        if last_number is None:
            return 1
        return int(last_number) + 1


def allocate_document_number(*, company_id: str, document_type_key: str, allocation_key: str) -> int:
    if not company_id:
        raise ValueError("company_id is required to allocate a document number")
    if not document_type_key:
        raise ValueError("document_type_key is required to allocate a document number")
    if not allocation_key:
        raise ValueError("allocation_key is required to allocate a document number")

    for _attempt in range(5):
        with session_scope() as session:
            existing = _get_allocated_number(
                session,
                company_id=company_id,
                document_type_key=document_type_key,
                allocation_key=allocation_key,
            )
            if existing is not None:
                return existing

            number = _next_counter_value(session, company_id, document_type_key)
            session.add(
                CompanyDocumentNumberAllocation(
                    company_id=company_id,
                    document_type_key=document_type_key,
                    allocation_key=allocation_key,
                    number=number,
                )
            )
            try:
                session.flush()
                return number
            except IntegrityError:
                continue

        with session_scope() as session:
            existing = _get_allocated_number(
                session,
                company_id=company_id,
                document_type_key=document_type_key,
                allocation_key=allocation_key,
            )
            if existing is not None:
                return existing

    raise RuntimeError("Failed to allocate document number after retries")
