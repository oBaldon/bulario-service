import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from bulario_service.models import (
    BularioDocumentArtifact,
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
