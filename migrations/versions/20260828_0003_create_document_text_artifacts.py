"""create bulario document text artifacts

Revision ID: 20260828_0003
Revises: 20260828_0002
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260828_0003"
down_revision: str | None = "20260828_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_text_artifacts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_artifact_id", sa.BigInteger(), nullable=False),
        sa.Column("extraction_method", sa.String(length=64), nullable=False),
        sa.Column("normalization_version", sa.String(length=32), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("character_count", sa.BigInteger(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_artifact_id"],
            ["bulario.document_artifacts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_artifact_id",
            "normalization_version",
            name="uq_bulario_document_text_artifact_version",
        ),
        schema="bulario",
    )
    op.create_index(
        "ix_bulario_document_text_artifacts_sha256",
        "document_text_artifacts",
        ["text_sha256"],
        unique=False,
        schema="bulario",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bulario_document_text_artifacts_sha256",
        table_name="document_text_artifacts",
        schema="bulario",
    )
    op.drop_table("document_text_artifacts", schema="bulario")
