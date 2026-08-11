"""Merge SMTP.BZ advisory and external service spend migration heads.

Revision ID: 0046_merge_smtpbz_spends
Revises: 0045_smtpbz_advisory, 0045_external_service_spends
Create Date: 2026-08-10
"""

from __future__ import annotations


revision = "0046_merge_smtpbz_spends"
down_revision = (
    "0045_smtpbz_advisory",
    "0045_external_service_spends",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
