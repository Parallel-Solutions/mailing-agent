"""Make SMTP.BZ validation results advisory.

Revision ID: 0045_smtpbz_advisory
Revises: 0044_delivery_guard_cycles
Create Date: 2026-08-10
"""

from __future__ import annotations

from alembic import op


revision = "0045_smtpbz_advisory"
down_revision = "0044_delivery_guard_cycles"
branch_labels = None
depends_on = None


def _clear_legacy_smtpbz_exclusions(table: str) -> None:
    op.execute(
        f"""
        UPDATE {table}
        SET
            excluded = false,
            extra = jsonb_set(extra, '{{validation_excluded}}', 'false'::jsonb, true)
        WHERE excluded IS TRUE
          AND extra @> '{{"validation_excluded": true}}'::jsonb
          AND COALESCE(extra -> 'email_validation' ->> 'provider', '') = 'smtpbz'
        """
    )


def upgrade() -> None:
    # SMTP.BZ used to exclude both inconclusive and negative probe results.
    # Restore those recipients now that the provider is advisory. Actual hard
    # bounces remain blocked per address by the validation cache/suppression.
    _clear_legacy_smtpbz_exclusions("audience_members")
    _clear_legacy_smtpbz_exclusions("campaign_recipients")


def downgrade() -> None:
    # The previous exclusions cannot be reconstructed reliably: users may
    # have edited recipients after the upgrade and SMTP.BZ results can expire.
    pass
