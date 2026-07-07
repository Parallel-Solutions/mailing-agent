from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import Workbook
from sqlalchemy import delete, select

from src.infra.db import session_scope
from src.infra.models import Client
from src.jobs.storage import normalize_job_id, resolve_job_paths


def prepare_data_xlsx(job_id: str | None, local_path: Path | None = None) -> Path:
    paths = resolve_job_paths(job_id)
    target = Path(local_path) if local_path is not None else paths.data_xlsx
    if normalize_job_id(job_id):
        return materialize_xlsx(job_id, target)
    return target


def sync_client_row(job_id: str | None, row: dict[str, Any]) -> None:
    row_index = row.get("_row_index")
    if row_index in (None, ""):
        return
    data = {
        str(key): "" if value is None else str(value)
        for key, value in row.items()
        if str(key) != "_row_index"
    }
    update_client(job_id, int(row_index), data)


def import_clients_from_xlsx(job_id: str | None, local_xlsx: Path) -> int:
    normalized = normalize_job_id(job_id)
    if not normalized:
        return 0
    df = pd.read_excel(local_xlsx, dtype=str).fillna("")
    now = datetime.now(timezone.utc)
    rows_written = 0
    with session_scope() as session:
        session.execute(delete(Client).where(Client.job_id == normalized))
        for index, row in df.iterrows():
            data = {str(col): str(row[col]) for col in df.columns}
            session.add(
                Client(
                    job_id=normalized,
                    row_index=int(index) + 1,
                    data=data,
                    updated_at=now,
                )
            )
            rows_written += 1
    return rows_written


def list_clients(job_id: str | None) -> list[dict[str, Any]]:
    normalized = normalize_job_id(job_id)
    if not normalized:
        return []
    with session_scope() as session:
        rows = session.execute(
            select(Client).where(Client.job_id == normalized).order_by(Client.row_index.asc())
        ).scalars().all()
    return [dict(row.data) if isinstance(row.data, dict) else {} for row in rows]


def update_client(job_id: str | None, row_index: int, data: dict[str, Any]) -> None:
    normalized = normalize_job_id(job_id)
    if not normalized:
        return
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.execute(
            select(Client).where(Client.job_id == normalized, Client.row_index == row_index)
        ).scalar_one_or_none()
        if row is None:
            session.add(Client(job_id=normalized, row_index=row_index, data=data, updated_at=now))
        else:
            row.data = data
            row.updated_at = now


def materialize_xlsx(job_id: str | None, local_path: Path) -> Path:
    normalized = normalize_job_id(job_id)
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    clients = list_clients(normalized)
    if not clients:
        if local_path.exists():
            return local_path
        wb = Workbook()
        wb.save(local_path)
        return local_path
    df = pd.DataFrame(clients)
    df.to_excel(local_path, index=False)
    return local_path
