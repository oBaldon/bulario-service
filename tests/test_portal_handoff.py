import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from bulario_service.models import (
    BularioDocumentArtifact,
    BularioDocumentTextArtifact,
    BularioDocumentVersion,
)
from bulario_service.portal_handoff import (
    PortalHandoffError,
    _parse_source_record_id,
    _validate_document_handoff,
)


def test_parse_source_record_id() -> None:
    assert _parse_source_record_id(
        "anvisa:1285139:35452428"
    ) == (1285139, 35452428)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "foo:1:2",
        "anvisa:x:2",
        "anvisa:1",
        "anvisa:0:2",
    ],
)
def test_parse_source_record_id_rejects_invalid(value: str) -> None:
    with pytest.raises(PortalHandoffError):
        _parse_source_record_id(value)


def test_validate_document_handoff_accepts_existing_pdf(tmp_path: Path) -> None:
    content = b"%PDF-1.7\nfake"
    sha = hashlib.sha256(content).hexdigest()
    key = "bulas/1/2/patient.pdf"
    path = tmp_path / key
    path.parent.mkdir(parents=True)
    path.write_bytes(content)

    artifact = BularioDocumentArtifact(
        id=1,
        document_version_id=2,
        kind="patient",
        storage_key=key,
        sha256=sha,
        size_bytes=len(content),
    )

    _validate_document_handoff(
        storage_root=tmp_path,
        public_storage_key=key,
        public_sha256=sha,
        operational_artifact=artifact,
        label="patient",
    )


def test_validate_document_handoff_rejects_public_hash_mismatch(
    tmp_path: Path,
) -> None:
    content = b"%PDF-1.7\nfake"
    sha = hashlib.sha256(content).hexdigest()
    key = "bulas/1/2/patient.pdf"
    path = tmp_path / key
    path.parent.mkdir(parents=True)
    path.write_bytes(content)

    artifact = BularioDocumentArtifact(
        id=1,
        document_version_id=2,
        kind="patient",
        storage_key=key,
        sha256=sha,
        size_bytes=len(content),
    )

    with pytest.raises(
        PortalHandoffError,
        match="public SHA-256 differs",
    ):
        _validate_document_handoff(
            storage_root=tmp_path,
            public_storage_key=key,
            public_sha256="0" * 64,
            operational_artifact=artifact,
            label="patient",
        )


def test_validate_document_handoff_rejects_missing_file(
    tmp_path: Path,
) -> None:
    artifact = BularioDocumentArtifact(
        id=1,
        document_version_id=2,
        kind="patient",
        storage_key="bulas/1/2/patient.pdf",
        sha256="a" * 64,
        size_bytes=100,
    )

    with pytest.raises(
        PortalHandoffError,
        match="does not exist",
    ):
        _validate_document_handoff(
            storage_root=tmp_path,
            public_storage_key=artifact.storage_key,
            public_sha256=artifact.sha256,
            operational_artifact=artifact,
            label="patient",
        )


def test_validate_document_handoff_rejects_non_pdf(tmp_path: Path) -> None:
    content = b"not a pdf"
    sha = hashlib.sha256(content).hexdigest()
    key = "bulas/1/2/patient.pdf"
    path = tmp_path / key
    path.parent.mkdir(parents=True)
    path.write_bytes(content)

    artifact = BularioDocumentArtifact(
        id=1,
        document_version_id=2,
        kind="patient",
        storage_key=key,
        sha256=sha,
        size_bytes=len(content),
    )

    with pytest.raises(
        PortalHandoffError,
        match="not a PDF",
    ):
        _validate_document_handoff(
            storage_root=tmp_path,
            public_storage_key=key,
            public_sha256=sha,
            operational_artifact=artifact,
            label="patient",
        )



class MappingRowsResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)


def test_validate_all_ready_handoffs_validates_every_row(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from bulario_service.portal_handoff import (
        PortalHandoffReport,
        validate_all_ready_handoffs,
    )

    rows = [
        {"id": 1, "source_record_id": "anvisa:1:10"},
        {"id": 2, "source_record_id": "anvisa:2:20"},
    ]
    session = Mock()
    session.execute.return_value = MappingRowsResult(rows)
    validated = []

    def validate(session_arg, *, row, storage_root):
        validated.append(row["id"])
        return PortalHandoffReport(
            public_row_id=row["id"],
            source_record_id=row["source_record_id"],
            source_product_id=row["id"],
            source_document_id=row["id"] * 10,
            ingestion_status="ready",
            patient_storage_key="patient.pdf",
            professional_storage_key="professional.pdf",
            patient_sha256="a" * 64,
            professional_sha256="b" * 64,
        )

    monkeypatch.setattr(
        "bulario_service.portal_handoff._validate_ready_handoff_row",
        validate,
    )

    reports = validate_all_ready_handoffs(
        session,
        storage_root=tmp_path,
    )

    assert validated == [1, 2]
    assert [report.public_row_id for report in reports] == [1, 2]



def test_validate_text_handoff_accepts_consistent_v1_text() -> None:
    from bulario_service.portal_handoff import _validate_text_handoff

    content = "Texto normalizado"
    artifact = BularioDocumentArtifact(
        id=7,
        document_version_id=2,
        kind="patient",
        storage_key="bulas/1/2/patient.pdf",
        sha256="a" * 64,
        size_bytes=100,
    )
    text_artifact = BularioDocumentTextArtifact(
        id=9,
        document_artifact_id=7,
        extraction_method="pdftotext-layout-utf8",
        normalization_version="v1",
        text_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        character_count=len(content),
        text_content=content,
    )
    session = Mock()
    session.scalar.return_value = text_artifact

    _validate_text_handoff(
        session,
        operational_artifact=artifact,
        label="patient",
    )


def test_validate_text_handoff_rejects_hash_mismatch() -> None:
    from bulario_service.portal_handoff import _validate_text_handoff

    artifact = BularioDocumentArtifact(
        id=7,
        document_version_id=2,
        kind="patient",
        storage_key="bulas/1/2/patient.pdf",
        sha256="a" * 64,
        size_bytes=100,
    )
    text_artifact = BularioDocumentTextArtifact(
        id=9,
        document_artifact_id=7,
        extraction_method="pdftotext-layout-utf8",
        normalization_version="v1",
        text_sha256="0" * 64,
        character_count=5,
        text_content="Texto",
    )
    session = Mock()
    session.scalar.return_value = text_artifact

    with pytest.raises(
        PortalHandoffError,
        match="text SHA-256",
    ):
        _validate_text_handoff(
            session,
            operational_artifact=artifact,
            label="patient",
        )
