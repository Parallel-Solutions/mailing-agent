"""Repair draft campaigns left in chain mode without a chain.

Revision ID: 0040_repair_detached_chains
Revises: 0039_smtp_open_tracking
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op


revision = "0040_repair_detached_chains"
down_revision = "0039_smtp_open_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE campaigns
        SET
            send_scenario = 'consent_then_materials',
            draft_payload = (
                jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            COALESCE(draft_payload, '{}'::jsonb),
                            '{send_scenario}',
                            '"consent_then_materials"'::jsonb,
                            true
                        ),
                        '{email_chain_id}',
                        'null'::jsonb,
                        true
                    ),
                    '{mapping_confirmed}',
                    'false'::jsonb,
                    true
                ) - 'mapping_confirmed_at'
            ),
            updated_at = now()
        WHERE status = 'draft'
          AND send_scenario = 'email_chain'
          AND email_chain_id IS NULL
          AND CASE
                WHEN jsonb_typeof(draft_payload -> 'email_chain' -> 'nodes') = 'array'
                THEN jsonb_array_length(draft_payload -> 'email_chain' -> 'nodes') = 0
                ELSE true
              END
        """
    )


def downgrade() -> None:
    pass
