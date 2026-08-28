import hashlib
from unittest.mock import Mock

import pytest
from sqlalchemy.sql.dml import Update

from bulario_service.anvisa import BulaVersion, DiscoveredProduct
from bulario_service.document_storage import StoredBulaDocument
from bulario_service.models import (
    BularioDocumentArtifact,
    BularioDocumentVersion,
    BularioProduct,
    IngestionItem,
)
from bulario_service.operational_persistence import (
    OperationalPersistenceConflictError,
    compute_source_fingerprint,
    persist_operational_version,
)


def make_product() -> DiscoveredProduct:
    return DiscoveredProduct(
        source_product_id=1174609,
        registration_number="123456789",
        product_name="Produto teste",
        current_expedient="111",
        company_name="Empresa teste",
        company_cnpj="00000000000000",
        process_number="25351000000000000",
        publication_date="2026-08-28",
        raw_payload={},
    )


def make_version(*, current: bool = True) -> BulaVersion:
    return BulaVersion(
        source_document_id=35480554,
        expedient="111",
        registration_number="123456789",
        publication_date="28/08/2026",
        status="Publicado",
        patient_token="temporary-patient-token",
        professional_token="temporary-professional-token",
        current=current,
        raw_payload={},
    )


def make_stored(kind: str = "patient") -> StoredBulaDocument:
    sha = hashlib.sha256(kind.encode()).hexdigest()
    return StoredBulaDocument(
        source_product_id=1174609,
        source_document_id=35480554,
        kind=kind,  # type: ignore[arg-type]
        storage_key=f"bulas/1174609/35480554/{kind}.pdf",
        sha256=sha,
        size_bytes=100,
    )


def test_source_fingerprint_excludes_transient_tokens_and_current_flag() -> None:
    product = make_product()
    version = make_version(current=True)
    fingerprint = compute_source_fingerprint(
        product=product,
        version=version,
    )

    changed = BulaVersion(
        source_document_id=version.source_document_id,
        expedient=version.expedient,
        registration_number=version.registration_number,
        publication_date=version.publication_date,
        status=version.status,
        patient_token="another-token",
        professional_token="another-token-2",
        current=False,
        raw_payload={"different": "transient raw payload"},
    )
    assert compute_source_fingerprint(
        product=product,
        version=changed,
    ) == fingerprint


def test_source_fingerprint_changes_with_stable_source_metadata() -> None:
    product = make_product()
    version = make_version()

    changed = BulaVersion(
        source_document_id=version.source_document_id,
        expedient="DIFFERENT",
        registration_number=version.registration_number,
        publication_date=version.publication_date,
        status=version.status,
        patient_token=version.patient_token,
        professional_token=version.professional_token,
        current=version.current,
        raw_payload={},
    )

    assert compute_source_fingerprint(
        product=product,
        version=version,
    ) != compute_source_fingerprint(
        product=product,
        version=changed,
    )


def test_persist_creates_product_version_and_artifacts() -> None:
    session = Mock()
    session.scalar.side_effect = [None, None, None, None]

    result = persist_operational_version(
        session,
        product=make_product(),
        version=make_version(),
        stored_documents=[
            make_stored("patient"),
            make_stored("professional"),
        ],
    )

    added = [call.args[0] for call in session.add.call_args_list]
    product = next(item for item in added if isinstance(item, BularioProduct))
    version = next(
        item for item in added if isinstance(item, BularioDocumentVersion)
    )
    artifacts = [
        item for item in added if isinstance(item, BularioDocumentArtifact)
    ]

    assert product.source_product_id == 1174609
    assert version.source_document_id == 35480554
    assert version.source_fingerprint
    assert len(artifacts) == 2
    assert {artifact.kind for artifact in artifacts} == {
        "patient",
        "professional",
    }
    assert result.product is product
    assert result.version is version


def test_existing_version_rejects_changed_fingerprint() -> None:
    product_model = BularioProduct(
        id=10,
        source_product_id=1174609,
        product_name="Produto teste",
    )
    version_model = BularioDocumentVersion(
        id=20,
        product_id=10,
        source_document_id=35480554,
        is_current=True,
        source_fingerprint="0" * 64,
    )

    session = Mock()
    session.scalar.side_effect = [product_model, version_model]

    with pytest.raises(
        OperationalPersistenceConflictError,
        match="source fingerprint changed",
    ):
        persist_operational_version(
            session,
            product=make_product(),
            version=make_version(),
            stored_documents=[],
        )


def test_existing_artifact_rejects_changed_hash() -> None:
    product = make_product()
    version = make_version()
    fingerprint = compute_source_fingerprint(
        product=product,
        version=version,
    )
    product_model = BularioProduct(
        id=10,
        source_product_id=1174609,
        product_name="Produto teste",
    )
    version_model = BularioDocumentVersion(
        id=20,
        product_id=10,
        source_document_id=35480554,
        is_current=True,
        source_fingerprint=fingerprint,
    )
    artifact_model = BularioDocumentArtifact(
        id=30,
        document_version_id=20,
        kind="patient",
        storage_key="bulas/1174609/35480554/patient.pdf",
        sha256="0" * 64,
        size_bytes=100,
    )

    session = Mock()
    session.scalar.side_effect = [
        product_model,
        version_model,
        artifact_model,
    ]

    with pytest.raises(
        OperationalPersistenceConflictError,
        match="stored artifact changed",
    ):
        persist_operational_version(
            session,
            product=product,
            version=version,
            stored_documents=[make_stored("patient")],
        )


def test_ingestion_item_must_be_persisted() -> None:
    with pytest.raises(ValueError, match="must be persisted"):
        persist_operational_version(
            Mock(),
            product=make_product(),
            version=make_version(),
            stored_documents=[],
            ingestion_item=IngestionItem(
                run_id=1,
                source_record_id="anvisa:1174609:35480554",
                status="downloaded",
            ),
        )


def test_stored_document_must_match_product_and_version() -> None:
    wrong = StoredBulaDocument(
        source_product_id=999,
        source_document_id=35480554,
        kind="patient",
        storage_key="bulas/999/35480554/patient.pdf",
        sha256="a" * 64,
        size_bytes=10,
    )

    with pytest.raises(ValueError, match="source_product_id"):
        persist_operational_version(
            Mock(),
            product=make_product(),
            version=make_version(),
            stored_documents=[wrong],
        )


def test_current_version_demotes_previous_current_versions() -> None:
    session = Mock()
    session.scalar.side_effect = [None, None]

    persist_operational_version(
        session,
        product=make_product(),
        version=make_version(current=True),
        stored_documents=[],
    )

    assert any(
        isinstance(call.args[0], Update)
        for call in session.execute.call_args_list
    )
