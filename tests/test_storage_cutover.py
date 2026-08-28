import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from bulario_service.models import BularioDocumentArtifact
from bulario_service.storage_cutover import (
    StorageCutoverError,
    reconcile_shared_storage,
)


class ScalarResult:
    def __init__(self, values):
        self._values = values

    def __iter__(self):
        return iter(self._values)


def artifact(*, key: str, content: bytes) -> BularioDocumentArtifact:
    return BularioDocumentArtifact(
        id=1,
        document_version_id=1,
        kind="patient",
        storage_key=key,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )


def session_for(*artifacts):
    session = Mock()
    session.scalars.return_value = ScalarResult(artifacts)
    return session


def test_dry_run_reports_pending_without_writing(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    content = b"%PDF-1.7\ncontent"
    key = "bulas/1/2/patient.pdf"

    source = source_root / key
    source.parent.mkdir(parents=True)
    source.write_bytes(content)

    report = reconcile_shared_storage(
        session_for(artifact(key=key, content=content)),
        source_root=source_root,
        target_root=target_root,
        write=False,
    )

    assert report.total_artifacts == 1
    assert report.pending_copy == 1
    assert report.copied == 0
    assert report.reused == 0
    assert not (target_root / key).exists()


def test_write_copies_and_verifies_pdf(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    content = b"%PDF-1.7\ncontent"
    key = "bulas/1/2/patient.pdf"

    source = source_root / key
    source.parent.mkdir(parents=True)
    source.write_bytes(content)

    report = reconcile_shared_storage(
        session_for(artifact(key=key, content=content)),
        source_root=source_root,
        target_root=target_root,
        write=True,
    )

    assert report.copied == 1
    assert report.pending_copy == 0
    assert (target_root / key).read_bytes() == content


def test_existing_identical_target_is_reused(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    content = b"%PDF-1.7\ncontent"
    key = "bulas/1/2/patient.pdf"

    target = target_root / key
    target.parent.mkdir(parents=True)
    target.write_bytes(content)

    report = reconcile_shared_storage(
        session_for(artifact(key=key, content=content)),
        source_root=source_root,
        target_root=target_root,
        write=True,
    )

    assert report.reused == 1
    assert report.copied == 0


def test_conflicting_target_is_rejected(tmp_path: Path) -> None:
    expected = b"%PDF-1.7\nexpected"
    key = "bulas/1/2/patient.pdf"
    target_root = tmp_path / "target"
    target = target_root / key
    target.parent.mkdir(parents=True)
    target.write_bytes(b"%PDF-1.7\nDIFFERENT")

    with pytest.raises(StorageCutoverError, match="hash mismatch"):
        reconcile_shared_storage(
            session_for(artifact(key=key, content=expected)),
            source_root=tmp_path / "source",
            target_root=target_root,
            write=True,
        )


def test_missing_from_both_roots_is_rejected(tmp_path: Path) -> None:
    content = b"%PDF-1.7\nexpected"

    with pytest.raises(StorageCutoverError, match="missing from both"):
        reconcile_shared_storage(
            session_for(
                artifact(
                    key="bulas/1/2/patient.pdf",
                    content=content,
                )
            ),
            source_root=tmp_path / "source",
            target_root=tmp_path / "target",
            write=False,
        )


def test_unsafe_storage_key_is_rejected(tmp_path: Path) -> None:
    content = b"%PDF-1.7\nexpected"

    with pytest.raises(StorageCutoverError, match="unsafe storage key"):
        reconcile_shared_storage(
            session_for(
                artifact(key="../escape.pdf", content=content)
            ),
            source_root=tmp_path / "source",
            target_root=tmp_path / "target",
            write=False,
        )
