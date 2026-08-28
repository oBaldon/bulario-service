from unittest.mock import Mock

import pytest

from bulario_service.document_text import ExtractedBulaText
from bulario_service.models import (
    BularioDocumentArtifact,
    BularioDocumentTextArtifact,
)
from bulario_service.operational_persistence import (
    OperationalPersistenceConflictError,
)
from bulario_service.operational_text_persistence import (
    persist_text_artifact,
)


def extracted(
    *,
    document_sha256: str = "a" * 64,
    text_sha256: str = "b" * 64,
    text: str = "Texto normalizado",
) -> ExtractedBulaText:
    return ExtractedBulaText(
        source_product_id=1174609,
        source_document_id=35480554,
        kind="patient",
        document_storage_key="bulas/1174609/35480554/patient.pdf",
        document_sha256=document_sha256,
        text=text,
        text_sha256=text_sha256,
        character_count=len(text),
    )


def artifact() -> BularioDocumentArtifact:
    return BularioDocumentArtifact(
        id=10,
        document_version_id=20,
        kind="patient",
        storage_key="bulas/1174609/35480554/patient.pdf",
        sha256="a" * 64,
        size_bytes=100,
    )


def test_persist_text_artifact_creates_derived_record() -> None:
    session = Mock()
    session.scalar.side_effect = [artifact(), None]

    result = persist_text_artifact(
        session,
        extracted=extracted(),
    )

    added = session.add.call_args.args[0]
    assert isinstance(added, BularioDocumentTextArtifact)
    assert added.document_artifact_id == 10
    assert added.normalization_version == "v1"
    assert added.extraction_method == "pdftotext-layout-utf8"
    assert added.text_content == "Texto normalizado"
    assert result.text_artifact is added


def test_persist_text_artifact_is_idempotent() -> None:
    existing = BularioDocumentTextArtifact(
        id=30,
        document_artifact_id=10,
        extraction_method="pdftotext-layout-utf8",
        normalization_version="v1",
        text_sha256="b" * 64,
        character_count=len("Texto normalizado"),
        text_content="Texto normalizado",
    )
    session = Mock()
    session.scalar.side_effect = [artifact(), existing]

    result = persist_text_artifact(
        session,
        extracted=extracted(),
    )

    session.add.assert_not_called()
    assert result.text_artifact is existing


def test_persist_text_artifact_rejects_pdf_provenance_mismatch() -> None:
    session = Mock()
    session.scalar.side_effect = [artifact()]

    with pytest.raises(
        OperationalPersistenceConflictError,
        match="text provenance does not match",
    ):
        persist_text_artifact(
            session,
            extracted=extracted(document_sha256="c" * 64),
        )


def test_persist_text_artifact_rejects_changed_text_same_version() -> None:
    existing = BularioDocumentTextArtifact(
        id=30,
        document_artifact_id=10,
        extraction_method="pdftotext-layout-utf8",
        normalization_version="v1",
        text_sha256="0" * 64,
        character_count=5,
        text_content="Outro",
    )
    session = Mock()
    session.scalar.side_effect = [artifact(), existing]

    with pytest.raises(
        OperationalPersistenceConflictError,
        match="text artifact changed",
    ):
        persist_text_artifact(
            session,
            extracted=extracted(),
        )


def test_persist_text_artifact_allows_new_normalization_version() -> None:
    session = Mock()
    session.scalar.side_effect = [artifact(), None]

    result = persist_text_artifact(
        session,
        extracted=extracted(),
        normalization_version="v2",
    )

    added = result.text_artifact
    assert added.normalization_version == "v2"


def test_persist_text_artifact_requires_existing_pdf_artifact() -> None:
    session = Mock()
    session.scalar.return_value = None

    with pytest.raises(
        ValueError,
        match="operational PDF artifact not found",
    ):
        persist_text_artifact(
            session,
            extracted=extracted(),
        )
