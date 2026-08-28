from dataclasses import dataclass, replace
import hashlib

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from bulario_service.anvisa import BulaVersion, DiscoveredProduct
from bulario_service.document_storage import StoredBulaDocument
from bulario_service.document_text import ExtractedBulaText
from bulario_service.models import (
    BularioDocumentArtifact,
    BularioDocumentTextArtifact,
    BularioDocumentVersion,
    BularioProduct,
)
from bulario_service.operational_persistence import (
    OperationalPersistenceConflictError,
    persist_operational_version,
)
from bulario_service.operational_text_persistence import persist_text_artifact
from bulario_service.publication_contract import (
    BulaPublicationCandidate,
    build_publication_candidate,
)
from bulario_service.publication_publisher import (
    BulaPublicationConflictError,
    publish_candidate,
)


class HardeningCheckError(RuntimeError):
    """Raised when an expected safety barrier is not observed."""


@dataclass(frozen=True)
class HardeningReport:
    source_record_id: str
    source_document_id: int
    public_rerun_unchanged: bool
    fingerprint_conflict_blocked: bool
    pdf_hash_conflict_blocked: bool
    text_conflict_blocked: bool
    operational_fingerprint_conflict_blocked: bool


def find_latest_published_current_document_id(session: Session) -> int:
    versions = tuple(
        session.scalars(
            select(BularioDocumentVersion)
            .where(BularioDocumentVersion.is_current.is_(True))
            .order_by(BularioDocumentVersion.id.desc())
        )
    )
    for version in versions:
        product = session.get(BularioProduct, version.product_id)
        if product is None:
            continue
        source_record_id = (
            f"anvisa:{product.source_product_id}:{version.source_document_id}"
        )
        count = session.execute(
            text(
                """
                SELECT count(*)
                FROM public.bulas
                WHERE source_record_id = :source_record_id
                  AND ingestion_status = 'ready'
                """
            ),
            {"source_record_id": source_record_id},
        ).scalar_one()
        if count == 1:
            return version.source_document_id

    raise HardeningCheckError(
        "no published current operational document was found"
    )


def run_hardening_checks(
    session: Session,
    *,
    source_document_id: int,
) -> HardeningReport:
    candidate = build_publication_candidate(
        session,
        source_document_id=source_document_id,
    )

    rerun = publish_candidate(session, candidate=candidate)
    if rerun.action != "unchanged":
        session.rollback()
        raise HardeningCheckError(
            "published candidate did not behave idempotently "
            f"action={rerun.action}"
        )
    session.rollback()

    _expect_publication_conflict(
        session,
        candidate=replace(
            candidate,
            source_fingerprint=_different_sha256(
                candidate.source_fingerprint
            ),
        ),
        expected_field="source_fingerprint",
    )

    _expect_publication_conflict(
        session,
        candidate=replace(
            candidate,
            patient=replace(
                candidate.patient,
                document_sha256=_different_sha256(
                    candidate.patient.document_sha256
                ),
            ),
        ),
        expected_field="bula_paciente_sha256",
    )

    _expect_text_conflict(
        session,
        candidate=candidate,
    )

    _expect_operational_fingerprint_conflict(
        session,
        candidate=candidate,
    )

    return HardeningReport(
        source_record_id=candidate.source_record_id,
        source_document_id=candidate.source_document_id,
        public_rerun_unchanged=True,
        fingerprint_conflict_blocked=True,
        pdf_hash_conflict_blocked=True,
        text_conflict_blocked=True,
        operational_fingerprint_conflict_blocked=True,
    )


def _expect_publication_conflict(
    session: Session,
    *,
    candidate: BulaPublicationCandidate,
    expected_field: str,
) -> None:
    try:
        publish_candidate(session, candidate=candidate)
    except BulaPublicationConflictError as exc:
        session.rollback()
        if expected_field not in str(exc):
            raise HardeningCheckError(
                "publication conflict did not identify expected field "
                f"field={expected_field} error={exc}"
            ) from exc
        return

    session.rollback()
    raise HardeningCheckError(
        "mutated published candidate was not blocked "
        f"expected_field={expected_field}"
    )


def _expect_text_conflict(
    session: Session,
    *,
    candidate: BulaPublicationCandidate,
) -> None:
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
            == candidate.source_product_id,
            BularioDocumentVersion.source_document_id
            == candidate.source_document_id,
            BularioDocumentArtifact.kind == "patient",
        )
    )
    if artifact is None:
        raise HardeningCheckError("patient PDF artifact not found")

    text_artifact = session.scalar(
        select(BularioDocumentTextArtifact).where(
            BularioDocumentTextArtifact.document_artifact_id == artifact.id,
            BularioDocumentTextArtifact.normalization_version
            == candidate.patient.normalization_version,
        )
    )
    if text_artifact is None:
        raise HardeningCheckError("patient text artifact not found")

    changed_text = text_artifact.text_content + "\n[hardening-conflict]"
    extracted = ExtractedBulaText(
        source_product_id=candidate.source_product_id,
        source_document_id=candidate.source_document_id,
        kind="patient",
        document_storage_key=artifact.storage_key,
        document_sha256=artifact.sha256,
        text=changed_text,
        text_sha256=hashlib.sha256(
            changed_text.encode("utf-8")
        ).hexdigest(),
        character_count=len(changed_text),
    )

    try:
        persist_text_artifact(
            session,
            extracted=extracted,
            extraction_method=text_artifact.extraction_method,
            normalization_version=text_artifact.normalization_version,
        )
    except OperationalPersistenceConflictError:
        session.rollback()
        return

    session.rollback()
    raise HardeningCheckError(
        "changed text under the same normalization version was not blocked"
    )


def _expect_operational_fingerprint_conflict(
    session: Session,
    *,
    candidate: BulaPublicationCandidate,
) -> None:
    version = session.scalar(
        select(BularioDocumentVersion).where(
            BularioDocumentVersion.source_document_id
            == candidate.source_document_id
        )
    )
    if version is None:
        raise HardeningCheckError("operational document version not found")

    product = session.get(BularioProduct, version.product_id)
    if product is None:
        raise HardeningCheckError("operational product not found")

    artifacts = tuple(
        session.scalars(
            select(BularioDocumentArtifact).where(
                BularioDocumentArtifact.document_version_id == version.id
            )
        )
    )

    discovered = DiscoveredProduct(
        source_product_id=product.source_product_id,
        registration_number=product.registration_number,
        product_name=product.product_name,
        current_expedient=version.expedient,
        company_name=product.company_name,
        company_cnpj=product.company_cnpj,
        process_number=product.process_number,
        publication_date=version.source_publication_date,
        raw_payload={},
    )
    changed_version = BulaVersion(
        source_document_id=version.source_document_id,
        expedient=(version.expedient or "") + "-hardening-conflict",
        registration_number=version.registration_number,
        publication_date=version.source_publication_date,
        status=version.status,
        patient_token=None,
        professional_token=None,
        current=version.is_current,
        raw_payload={},
    )
    stored = tuple(
        StoredBulaDocument(
            source_product_id=product.source_product_id,
            source_document_id=version.source_document_id,
            kind=artifact.kind,
            storage_key=artifact.storage_key,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
        )
        for artifact in artifacts
    )

    try:
        persist_operational_version(
            session,
            product=discovered,
            version=changed_version,
            stored_documents=stored,
        )
    except OperationalPersistenceConflictError:
        session.rollback()
        return

    session.rollback()
    raise HardeningCheckError(
        "material metadata change under the same source_document_id "
        "was not blocked"
    )


def _different_sha256(value: str) -> str:
    replacement = "0" if value[0] != "0" else "1"
    return replacement + value[1:]
