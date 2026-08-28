import hashlib
from pathlib import Path

import pytest

from bulario_service.anvisa_documents import DownloadedBulaDocument
from bulario_service.document_storage import (
    DocumentStorageConflictError,
    DocumentStorageError,
    LocalDocumentStorage,
)


PDF_BYTES = b"%PDF-1.7\nstored test\n%%EOF"


def make_document(
    *,
    source_document_id: int = 35480554,
    kind: str = "patient",
    content: bytes = PDF_BYTES,
) -> DownloadedBulaDocument:
    return DownloadedBulaDocument(
        source_document_id=source_document_id,
        kind=kind,  # type: ignore[arg-type]
        content=content,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        content_type="application/pdf",
    )


def test_build_storage_key_is_relative_and_deterministic(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)

    key = storage.build_storage_key(
        source_product_id=1174609,
        source_document_id=35480554,
        kind="patient",
    )

    assert key == "bulas/1174609/35480554/patient.pdf"
    assert not key.startswith("/")


def test_store_writes_document_and_verifies_hash(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)
    document = make_document()

    stored = storage.store(
        source_product_id=1174609,
        document=document,
    )

    assert stored.storage_key == (
        "bulas/1174609/35480554/patient.pdf"
    )
    assert stored.sha256 == document.sha256
    assert stored.size_bytes == len(PDF_BYTES)

    path = storage.resolve(stored.storage_key)
    assert path.read_bytes() == PDF_BYTES


def test_store_is_idempotent_for_same_hash(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)
    document = make_document()

    first = storage.store(
        source_product_id=1174609,
        document=document,
    )
    second = storage.store(
        source_product_id=1174609,
        document=document,
    )

    assert second == first
    assert storage.resolve(second.storage_key).read_bytes() == PDF_BYTES


def test_store_rejects_same_key_with_different_content(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)
    original = make_document(content=PDF_BYTES)
    changed = make_document(content=b"%PDF-1.7\nchanged\n%%EOF")

    storage.store(
        source_product_id=1174609,
        document=original,
    )

    with pytest.raises(
        DocumentStorageConflictError,
        match="storage conflict",
    ):
        storage.store(
            source_product_id=1174609,
            document=changed,
        )


def test_resolve_rejects_path_escape(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)

    with pytest.raises(DocumentStorageError, match="unsafe storage key"):
        storage.resolve("../outside.pdf")


@pytest.mark.parametrize("kind", ["other", "", "PATIENT"])
def test_build_storage_key_rejects_unknown_kind(
    tmp_path: Path,
    kind: str,
) -> None:
    storage = LocalDocumentStorage(tmp_path)

    with pytest.raises(ValueError, match="kind"):
        storage.build_storage_key(
            source_product_id=1,
            source_document_id=1,
            kind=kind,  # type: ignore[arg-type]
        )


def test_atomic_write_does_not_leave_temp_file(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)
    document = make_document(kind="professional")

    stored = storage.store(
        source_product_id=1174609,
        document=document,
    )

    directory = storage.resolve(stored.storage_key).parent
    names = [path.name for path in directory.iterdir()]

    assert names == ["professional.pdf"]
