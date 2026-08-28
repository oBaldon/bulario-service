from datetime import datetime, timezone
from unittest.mock import Mock
import hashlib

import pytest

from bulario_service.models import (
    BularioDocumentArtifact,
    BularioDocumentTextArtifact,
    BularioDocumentVersion,
    BularioProduct,
)
from bulario_service.publication_contract import (
    BulaPublicationCandidate,
    BulaPublicationContractError,
    PublicationDocument,
    build_publication_candidate,
    validate_publication_candidate,
)


TEXT_PATIENT = "Texto paciente"
TEXT_PRO = "Texto profissional"


def text_artifact(artifact_id: int, text: str):
    return BularioDocumentTextArtifact(
        id=artifact_id + 100,
        document_artifact_id=artifact_id,
        extraction_method="pdftotext-layout-utf8",
        normalization_version="v1",
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        character_count=len(text),
        text_content=text,
    )


def candidate() -> BulaPublicationCandidate:
    def doc(kind: str, text: str) -> PublicationDocument:
        return PublicationDocument(
            kind=kind,
            storage_key=f"bulas/1/2/{kind}.pdf",
            document_sha256="a" * 64,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            text_content=text,
            character_count=len(text),
            extraction_method="pdftotext-layout-utf8",
            normalization_version="v1",
        )

    return BulaPublicationCandidate(
        source="ANVISA_BULARIO",
        source_record_id="anvisa:1:2",
        source_url="https://consultas.anvisa.gov.br/#/bulario/",
        source_product_id=1,
        source_document_id=2,
        source_fingerprint="b" * 64,
        ingested_at=datetime.now(timezone.utc),
        ingestion_status="ready",
        registration_number="123",
        product_name="Produto",
        company_name="Empresa",
        company_cnpj="00000000000000",
        process_number="25351",
        expedient="456",
        source_publication_date="28/08/2026",
        patient=doc("patient", TEXT_PATIENT),
        professional=doc("professional", TEXT_PRO),
    )


def test_valid_candidate_is_accepted() -> None:
    validate_publication_candidate(candidate())


def test_non_ready_candidate_is_rejected() -> None:
    original = candidate()
    bad = BulaPublicationCandidate(
        **{**original.__dict__, "ingestion_status": "normalized"}
    )
    with pytest.raises(
        BulaPublicationContractError,
        match="must be ready",
    ):
        validate_publication_candidate(bad)


def test_unsafe_storage_key_is_rejected() -> None:
    original = candidate()
    bad_patient = PublicationDocument(
        **{**original.patient.__dict__, "storage_key": "../secret.pdf"}
    )
    bad = BulaPublicationCandidate(
        **{**original.__dict__, "patient": bad_patient}
    )
    with pytest.raises(
        BulaPublicationContractError,
        match="unsafe PDF storage key",
    ):
        validate_publication_candidate(bad)


def test_text_hash_mismatch_is_rejected() -> None:
    original = candidate()
    bad_patient = PublicationDocument(
        **{**original.patient.__dict__, "text_sha256": "0" * 64}
    )
    bad = BulaPublicationCandidate(
        **{**original.__dict__, "patient": bad_patient}
    )
    with pytest.raises(
        BulaPublicationContractError,
        match="does not match text",
    ):
        validate_publication_candidate(bad)


def test_build_candidate_requires_both_document_kinds() -> None:
    version = BularioDocumentVersion(
        id=20,
        product_id=10,
        source_document_id=2,
        source_fingerprint="b" * 64,
        first_seen_at=datetime.now(timezone.utc),
    )
    product = BularioProduct(
        id=10,
        source_product_id=1,
        product_name="Produto",
    )
    patient = BularioDocumentArtifact(
        id=30,
        document_version_id=20,
        kind="patient",
        storage_key="bulas/1/2/patient.pdf",
        sha256="a" * 64,
        size_bytes=100,
    )

    session = Mock()
    session.scalar.return_value = version
    session.get.return_value = product
    session.scalars.return_value = [patient]

    with pytest.raises(
        BulaPublicationContractError,
        match="exactly patient and professional",
    ):
        build_publication_candidate(
            session,
            source_document_id=2,
        )


def test_build_candidate_materializes_complete_ready_payload() -> None:
    now = datetime.now(timezone.utc)
    version = BularioDocumentVersion(
        id=20,
        product_id=10,
        source_document_id=2,
        source_fingerprint="b" * 64,
        first_seen_at=now,
        registration_number="123",
        expedient="456",
        source_publication_date="28/08/2026",
    )
    product = BularioProduct(
        id=10,
        source_product_id=1,
        product_name="Produto",
        company_name="Empresa",
    )
    patient = BularioDocumentArtifact(
        id=30,
        document_version_id=20,
        kind="patient",
        storage_key="bulas/1/2/patient.pdf",
        sha256="a" * 64,
        size_bytes=100,
    )
    professional = BularioDocumentArtifact(
        id=31,
        document_version_id=20,
        kind="professional",
        storage_key="bulas/1/2/professional.pdf",
        sha256="c" * 64,
        size_bytes=120,
    )

    patient_text = text_artifact(30, TEXT_PATIENT)
    pro_text = text_artifact(31, TEXT_PRO)

    session = Mock()
    session.scalar.return_value = version
    session.get.return_value = product
    session.scalars.side_effect = [
        [patient, professional],
        [patient_text],
        [pro_text],
    ]

    result = build_publication_candidate(
        session,
        source_document_id=2,
    )

    assert result.source_record_id == "anvisa:1:2"
    assert result.ingestion_status == "ready"
    assert result.patient.text_content == TEXT_PATIENT
    assert result.professional.text_content == TEXT_PRO
