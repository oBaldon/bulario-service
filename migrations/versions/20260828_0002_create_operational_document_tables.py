"""create bulario operational document tables

Revision ID: 20260828_0002
Revises: 20260828_0001
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260828_0002"
down_revision: str | None = "20260828_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_product_id", sa.BigInteger(), nullable=False),
        sa.Column("registration_number", sa.String(length=64), nullable=True),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=True),
        sa.Column("company_cnpj", sa.String(length=32), nullable=True),
        sa.Column("process_number", sa.String(length=64), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_product_id",
            name="uq_bulario_products_source_product_id",
        ),
        schema="bulario",
    )

    op.create_table(
        "document_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("last_ingestion_item_id", sa.BigInteger(), nullable=True),
        sa.Column("source_document_id", sa.BigInteger(), nullable=False),
        sa.Column("expedient", sa.String(length=64), nullable=True),
        sa.Column("registration_number", sa.String(length=64), nullable=True),
        sa.Column("source_publication_date", sa.String(length=32), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column(
            "is_current",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["bulario.products.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["last_ingestion_item_id"],
            ["bulario.ingestion_items.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_document_id",
            name="uq_bulario_document_versions_source_document_id",
        ),
        schema="bulario",
    )
    op.create_index(
        "ix_bulario_document_versions_product_id",
        "document_versions",
        ["product_id"],
        unique=False,
        schema="bulario",
    )

    op.create_table(
        "document_artifacts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_version_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["bulario.document_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_version_id",
            "kind",
            name="uq_bulario_document_artifacts_version_kind",
        ),
        sa.UniqueConstraint(
            "storage_key",
            name="uq_bulario_document_artifacts_storage_key",
        ),
        schema="bulario",
    )
    op.create_index(
        "ix_bulario_document_artifacts_sha256",
        "document_artifacts",
        ["sha256"],
        unique=False,
        schema="bulario",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bulario_document_artifacts_sha256",
        table_name="document_artifacts",
        schema="bulario",
    )
    op.drop_table("document_artifacts", schema="bulario")

    op.drop_index(
        "ix_bulario_document_versions_product_id",
        table_name="document_versions",
        schema="bulario",
    )
    op.drop_table("document_versions", schema="bulario")
    op.drop_table("products", schema="bulario")
