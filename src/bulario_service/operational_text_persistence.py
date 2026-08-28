from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from bulario_service.document_text import (
    ExtractedBulaText,
    PDF_TEXT_EXTRACTION_METHOD,
    TEXT_NORMALIZATION_VERSION,
)
from bulario_service.models import (
    BularioDocumentArtifact,
    BularioDocumentTextArtifact,
    BularioDocumentVersion,
    BularioProduct,
)
from bulario_service.operational_persistence import (
    OperationalPersistenceConflictError,
)


@dataclass(frozen=True)
class PersistedTextArtifact:
    artifact: BularioDocumentArtifact
    text_artifact: BularioDocumentTextArtifact


def persist_text_artifact(
    session: Session,
    *,
    extracted: ExtractedBulaText,
    extraction_method: str = PDF_TEXT_EXTRACTION_METHOD,
    normalization_version: str = TEXT_NORMALIZATION_VERSION,
) -> PersistedTextArtifact:
    if not extraction_method:
        raise ValueError("extraction_method is required")
    if not normalization_version:
        raise ValueError("normalization_version is required")

    artifact = session.scalar(
        select(BularioDocumentArtifact)
        .join(
            BularioDocumentVersion,
            BularioDocumentVersion.id
            == BularioDocumentArtifact.document_version_id,
        )
        .join(
            BularioProduct,
            BularioProduct.id == BularioDocumentVersion.product_id,
        )
        .where(
            BularioProduct.source_product_id
            == extracted.source_product_id,
            BularioDocumentVersion.source_document_id
            == extracted.source_document_id,
            BularioDocumentArtifact.kind == extracted.kind,
        )
    )
    if artifact is None:
        raise ValueError(
            "operational PDF artifact not found for extracted text"
        )

    _assert_pdf_provenance_matches(
        artifact=artifact,
        extracted=extracted,
    )

    existing = session.scalar(
        select(BularioDocumentTextArtifact).where(
            BularioDocumentTextArtifact.document_artifact_id
            == artifact.id,
            BularioDocumentTextArtifact.normalization_version
            == normalization_version,
        )
    )

    if existing is None:
        text_artifact = BularioDocumentTextArtifact(
            document_artifact_id=artifact.id,
            extraction_method=extraction_method,
            normalization_version=normalization_version,
            text_sha256=extracted.text_sha256,
            character_count=extracted.character_count,
            text_content=extracted.text,
        )
        session.add(text_artifact)
        session.flush()
    else:
        _assert_text_artifact_unchanged(
            existing=existing,
            extracted=extracted,
            extraction_method=extraction_method,
        )
        text_artifact = existing

    return PersistedTextArtifact(
        artifact=artifact,
        text_artifact=text_artifact,
    )


def _assert_pdf_provenance_matches(
    *,
    artifact: BularioDocumentArtifact,
    extracted: ExtractedBulaText,
) -> None:
    if (
        artifact.sha256 != extracted.document_sha256
        or artifact.storage_key != extracted.document_storage_key
    ):
        raise OperationalPersistenceConflictError(
            "text provenance does not match persisted PDF artifact "
            f"source_document_id={extracted.source_document_id} "
            f"kind={extracted.kind}"
        )


def _assert_text_artifact_unchanged(
    *,
    existing: BularioDocumentTextArtifact,
    extracted: ExtractedBulaText,
    extraction_method: str,
) -> None:
    if (
        existing.text_sha256 != extracted.text_sha256
        or existing.character_count != extracted.character_count
        or existing.text_content != extracted.text
        or existing.extraction_method != extraction_method
    ):
        raise OperationalPersistenceConflictError(
            "text artifact changed for existing normalization version "
            f"document_artifact_id={existing.document_artifact_id} "
            f"normalization_version={existing.normalization_version}"
        )
