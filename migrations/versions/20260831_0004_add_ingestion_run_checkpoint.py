"""add ingestion run checkpoint metadata

Revision ID: 20260831_0004
Revises: 20260828_0003
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260831_0004"
down_revision: str | None = "20260828_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_runs",
        sa.Column("mode", sa.String(length=32), nullable=True),
        schema="bulario",
    )
    op.add_column(
        "ingestion_runs",
        sa.Column("period_start", sa.String(length=64), nullable=True),
        schema="bulario",
    )
    op.add_column(
        "ingestion_runs",
        sa.Column("period_end", sa.String(length=64), nullable=True),
        schema="bulario",
    )
    op.add_column(
        "ingestion_runs",
        sa.Column("page_size", sa.Integer(), nullable=True),
        schema="bulario",
    )
    op.add_column(
        "ingestion_runs",
        sa.Column(
            "last_completed_page",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema="bulario",
    )
    op.add_column(
        "ingestion_runs",
        sa.Column(
            "last_checkpoint_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="bulario",
    )


def downgrade() -> None:
    op.drop_column(
        "ingestion_runs",
        "last_checkpoint_at",
        schema="bulario",
    )
    op.drop_column(
        "ingestion_runs",
        "last_completed_page",
        schema="bulario",
    )
    op.drop_column("ingestion_runs", "page_size", schema="bulario")
    op.drop_column("ingestion_runs", "period_end", schema="bulario")
    op.drop_column("ingestion_runs", "period_start", schema="bulario")
    op.drop_column("ingestion_runs", "mode", schema="bulario")
