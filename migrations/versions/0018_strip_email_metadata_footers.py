"""Strip service metadata footers from saved email templates.

Revision ID: 0018_strip_email_footers
Revises: 0017_on_error_skip
Create Date: 2026-07-21
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa

from src.campaigns.template_footer_utils import strip_email_metadata_footer

revision = "0018_strip_email_footers"
down_revision = "0017_on_error_skip"
branch_labels = None
depends_on = None


def _clean_text(value: str | None) -> str | None:
    if not value or not str(value).strip():
        return value
    cleaned = strip_email_metadata_footer(str(value))
    return cleaned if cleaned != str(value) else value


def upgrade() -> None:
    bind = op.get_bind()

    template_rows = bind.execute(
        sa.text(
            """
            SELECT tv.id, tv.body_html, tv.body_text
            FROM template_versions tv
            JOIN mail_templates mt ON mt.id = tv.template_id
            WHERE mt.template_type = 'email'
            """
        )
    ).mappings()

    for row in template_rows:
        body_html = _clean_text(row["body_html"])
        body_text = _clean_text(row["body_text"])
        if body_html != row["body_html"] or body_text != row["body_text"]:
            bind.execute(
                sa.text(
                    """
                    UPDATE template_versions
                    SET body_html = :body_html, body_text = :body_text
                    WHERE id = :id
                    """
                ),
                {"id": row["id"], "body_html": body_html, "body_text": body_text or ""},
            )

    campaign_rows = bind.execute(
        sa.text("SELECT id, draft_payload FROM campaigns WHERE draft_payload IS NOT NULL")
    ).mappings()

    for row in campaign_rows:
        draft = dict(row["draft_payload"] or {})
        changed = False
        for key in ("email_body", "email_body_text"):
            if key not in draft:
                continue
            cleaned = _clean_text(str(draft.get(key) or ""))
            if cleaned != draft.get(key):
                draft[key] = cleaned
                changed = True
        if changed:
            bind.execute(
                sa.text("UPDATE campaigns SET draft_payload = :draft_payload WHERE id = :id"),
                {
                    "id": row["id"],
                    "draft_payload": json.dumps(draft, ensure_ascii=False),
                },
            )


def downgrade() -> None:
    pass
