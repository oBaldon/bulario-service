from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = {"schema": "bulario"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionItem(Base):
    __tablename__ = "ingestion_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "source_record_id",
            name="uq_ingestion_items_run_source_record",
        ),
        Index("ix_ingestion_items_source_record_id", "source_record_id"),
        {"schema": "bulario"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("bulario.ingestion_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    normalized_payload: Mapped[dict | None] = mapped_column(JSONB)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )



class BularioProduct(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint(
            "source_product_id",
            name="uq_bulario_products_source_product_id",
        ),
        {"schema": "bulario"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_product_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    registration_number: Mapped[str | None] = mapped_column(String(64))
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    company_name: Mapped[str | None] = mapped_column(Text)
    company_cnpj: Mapped[str | None] = mapped_column(String(32))
    process_number: Mapped[str | None] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class BularioDocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            name="uq_bulario_document_versions_source_document_id",
        ),
        Index(
            "ix_bulario_document_versions_product_id",
            "product_id",
        ),
        {"schema": "bulario"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("bulario.products.id", ondelete="CASCADE"),
        nullable=False,
    )
    last_ingestion_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("bulario.ingestion_items.id", ondelete="SET NULL"),
    )
    source_document_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expedient: Mapped[str | None] = mapped_column(String(64))
    registration_number: Mapped[str | None] = mapped_column(String(64))
    source_publication_date: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class BularioDocumentArtifact(Base):
    __tablename__ = "document_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "kind",
            name="uq_bulario_document_artifacts_version_kind",
        ),
        UniqueConstraint(
            "storage_key",
            name="uq_bulario_document_artifacts_storage_key",
        ),
        Index(
            "ix_bulario_document_artifacts_sha256",
            "sha256",
        ),
        {"schema": "bulario"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_version_id: Mapped[int] = mapped_column(
        ForeignKey("bulario.document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )



class BularioDocumentTextArtifact(Base):
    __tablename__ = "document_text_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "document_artifact_id",
            "normalization_version",
            name="uq_bulario_document_text_artifact_version",
        ),
        Index(
            "ix_bulario_document_text_artifacts_sha256",
            "text_sha256",
        ),
        {"schema": "bulario"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_artifact_id: Mapped[int] = mapped_column(
        ForeignKey("bulario.document_artifacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    extraction_method: Mapped[str] = mapped_column(String(64), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(32), nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    character_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
