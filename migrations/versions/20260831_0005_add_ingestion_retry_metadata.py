"""add ingestion item retry metadata

Revision ID: 20260831_0005
Revises: 20260831_0004
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260831_0005"
down_revision: str | None = "20260831_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_items",
        sa.Column("error_class", sa.String(length=32), nullable=True),
        schema="bulario",
    )
    op.add_column(
        "ingestion_items",
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        schema="bulario",
    )


def downgrade() -> None:
    op.drop_column(
        "ingestion_items",
        "retry_count",
        schema="bulario",
    )
    op.drop_column(
        "ingestion_items",
        "error_class",
        schema="bulario",
    )
