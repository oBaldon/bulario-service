from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from bulario_service.models import (
    BularioDocumentArtifact,
    BularioDocumentTextArtifact,
    BularioDocumentVersion,
    BularioProduct,
)


SOURCE_NAME = "ANVISA_BULARIO"
SOURCE_URL = "https://consultas.anvisa.gov.br/#/bulario/"
READY_STATUS = "ready"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BulaPublicationContractError(RuntimeError):
    """Raised when an operational record cannot satisfy the publication contract."""


@dataclass(frozen=True)
class PublicationDocument:
    kind: str
    storage_key: str
    document_sha256: str
    text_sha256: str
    text_content: str
    character_count: int
    extraction_method: str
    normalization_version: str


@dataclass(frozen=True)
class BulaPublicationCandidate:
    source: str
    source_record_id: str
    source_url: str
    source_product_id: int
    source_document_id: int
    source_fingerprint: str
    ingested_at: datetime
    ingestion_status: str
    registration_number: str | None
    product_name: str
    company_name: str | None
    company_cnpj: str | None
    process_number: str | None
    expedient: str | None
    source_publication_date: str | None
    patient: PublicationDocument
    professional: PublicationDocument


def build_publication_candidate(
    session: Session,
    *,
    source_document_id: int,
) -> BulaPublicationCandidate:
    version = session.scalar(
        select(BularioDocumentVersion).where(
            BularioDocumentVersion.source_document_id == source_document_id
        )
    )
    if version is None:
        raise BulaPublicationContractError(
            f"document version not found source_document_id={source_document_id}"
        )

    product = session.get(BularioProduct, version.product_id)
    if product is None:
        raise BulaPublicationContractError(
            f"product not found for source_document_id={source_document_id}"
        )

    artifacts = tuple(
        session.scalars(
            select(BularioDocumentArtifact).where(
                BularioDocumentArtifact.document_version_id == version.id
            )
        )
    )
    by_kind = {artifact.kind: artifact for artifact in artifacts}

    if set(by_kind) != {"patient", "professional"}:
        raise BulaPublicationContractError(
            "ready publication requires exactly patient and professional PDFs "
            f"source_document_id={source_document_id}"
        )

    patient = _build_document(session, by_kind["patient"])
    professional = _build_document(session, by_kind["professional"])

    candidate = BulaPublicationCandidate(
        source=SOURCE_NAME,
        source_record_id=(
            f"anvisa:{product.source_product_id}:{version.source_document_id}"
        ),
        source_url=SOURCE_URL,
        source_product_id=product.source_product_id,
        source_document_id=version.source_document_id,
        source_fingerprint=version.source_fingerprint,
        ingested_at=_ensure_timezone(version.first_seen_at),
        ingestion_status=READY_STATUS,
        registration_number=(
            version.registration_number or product.registration_number
        ),
        product_name=product.product_name,
        company_name=product.company_name,
        company_cnpj=product.company_cnpj,
        process_number=product.process_number,
        expedient=version.expedient,
        source_publication_date=version.source_publication_date,
        patient=patient,
        professional=professional,
    )
    validate_publication_candidate(candidate)
    return candidate


def validate_publication_candidate(
    candidate: BulaPublicationCandidate,
) -> None:
    if candidate.ingestion_status != READY_STATUS:
        raise BulaPublicationContractError(
            "public ingestion_status must be ready"
        )
    if not candidate.source_record_id:
        raise BulaPublicationContractError("source_record_id is required")
    if not candidate.source_url.startswith("https://"):
        raise BulaPublicationContractError("source_url must use https")
    if candidate.source_product_id < 1 or candidate.source_document_id < 1:
        raise BulaPublicationContractError("source identities must be positive")
    _require_sha256(
        "source_fingerprint",
        candidate.source_fingerprint,
    )
    if not candidate.product_name.strip():
        raise BulaPublicationContractError("product_name is required")
    if candidate.ingested_at.tzinfo is None:
        raise BulaPublicationContractError(
            "ingested_at must be timezone-aware"
        )

    if candidate.patient.kind != "patient":
        raise BulaPublicationContractError(
            "patient document has invalid kind"
        )
    if candidate.professional.kind != "professional":
        raise BulaPublicationContractError(
            "professional document has invalid kind"
        )

    _validate_document(candidate.patient)
    _validate_document(candidate.professional)


def _build_document(
    session: Session,
    artifact: BularioDocumentArtifact,
) -> PublicationDocument:
    text_artifacts = tuple(
        session.scalars(
            select(BularioDocumentTextArtifact)
            .where(
                BularioDocumentTextArtifact.document_artifact_id
                == artifact.id
            )
            .order_by(BularioDocumentTextArtifact.id.desc())
        )
    )
    if len(text_artifacts) != 1:
        raise BulaPublicationContractError(
            "ready publication requires exactly one persisted text artifact "
            f"document_artifact_id={artifact.id}"
        )

    text_artifact = text_artifacts[0]
    document = PublicationDocument(
        kind=artifact.kind,
        storage_key=artifact.storage_key,
        document_sha256=artifact.sha256,
        text_sha256=text_artifact.text_sha256,
        text_content=text_artifact.text_content,
        character_count=text_artifact.character_count,
        extraction_method=text_artifact.extraction_method,
        normalization_version=text_artifact.normalization_version,
    )
    _validate_document(document)
    return document


def _validate_document(document: PublicationDocument) -> None:
    _validate_storage_key(document.storage_key)
    _require_sha256("document_sha256", document.document_sha256)
    _require_sha256("text_sha256", document.text_sha256)

    if not document.text_content:
        raise BulaPublicationContractError(
            f"{document.kind} text content is required"
        )
    if len(document.text_content) != document.character_count:
        raise BulaPublicationContractError(
            f"{document.kind} character_count does not match text"
        )
    computed_text_sha256 = hashlib.sha256(
        document.text_content.encode("utf-8")
    ).hexdigest()
    if computed_text_sha256 != document.text_sha256:
        raise BulaPublicationContractError(
            f"{document.kind} text_sha256 does not match text"
        )
    if not document.extraction_method:
        raise BulaPublicationContractError(
            f"{document.kind} extraction_method is required"
        )
    if not document.normalization_version:
        raise BulaPublicationContractError(
            f"{document.kind} normalization_version is required"
        )


def _validate_storage_key(storage_key: str) -> None:
    if not storage_key or "\\" in storage_key:
        raise BulaPublicationContractError("invalid PDF storage key")

    path = PurePosixPath(storage_key)
    if path.is_absolute() or ".." in path.parts:
        raise BulaPublicationContractError("unsafe PDF storage key")
    if path.suffix.lower() != ".pdf":
        raise BulaPublicationContractError(
            "PDF storage key must end with .pdf"
        )


def _require_sha256(field: str, value: str) -> None:
    if not SHA256_RE.fullmatch(value):
        raise BulaPublicationContractError(
            f"{field} must be a lowercase SHA-256 hex digest"
        )


def _ensure_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
