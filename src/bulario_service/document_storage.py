from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile

from bulario_service.anvisa_documents import (
    DocumentKind,
    DownloadedBulaDocument,
)


class DocumentStorageError(RuntimeError):
    """Base error for document storage operations."""


class DocumentStorageConflictError(DocumentStorageError):
    """Raised when a deterministic storage key already has different bytes."""


@dataclass(frozen=True)
class StoredBulaDocument:
    source_product_id: int
    source_document_id: int
    kind: DocumentKind
    storage_key: str
    sha256: str
    size_bytes: int


class LocalDocumentStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def build_storage_key(
        self,
        *,
        source_product_id: int,
        source_document_id: int,
        kind: DocumentKind,
    ) -> str:
        if source_product_id < 1:
            raise ValueError(
                "source_product_id must be greater than or equal to 1"
            )
        if source_document_id < 1:
            raise ValueError(
                "source_document_id must be greater than or equal to 1"
            )
        if kind not in {"patient", "professional"}:
            raise ValueError("kind must be patient or professional")

        return (
            f"bulas/{source_product_id}/"
            f"{source_document_id}/{kind}.pdf"
        )

    def store(
        self,
        *,
        source_product_id: int,
        document: DownloadedBulaDocument,
    ) -> StoredBulaDocument:
        storage_key = self.build_storage_key(
            source_product_id=source_product_id,
            source_document_id=document.source_document_id,
            kind=document.kind,
        )

        target = self._resolve_storage_key(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            existing_sha256, existing_size = _hash_file(target)
            if existing_sha256 != document.sha256:
                raise DocumentStorageConflictError(
                    "document storage conflict "
                    f"storage_key={storage_key} "
                    f"expected_sha256={document.sha256} "
                    f"existing_sha256={existing_sha256}"
                )

            if existing_size != document.size_bytes:
                raise DocumentStorageConflictError(
                    "document storage size conflict "
                    f"storage_key={storage_key}"
                )

            return StoredBulaDocument(
                source_product_id=source_product_id,
                source_document_id=document.source_document_id,
                kind=document.kind,
                storage_key=storage_key,
                sha256=existing_sha256,
                size_bytes=existing_size,
            )

        self._atomic_write(
            target=target,
            content=document.content,
        )

        stored_sha256, stored_size = _hash_file(target)
        if stored_sha256 != document.sha256:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            raise DocumentStorageError(
                "stored document hash verification failed "
                f"storage_key={storage_key}"
            )

        if stored_size != document.size_bytes:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            raise DocumentStorageError(
                "stored document size verification failed "
                f"storage_key={storage_key}"
            )

        return StoredBulaDocument(
            source_product_id=source_product_id,
            source_document_id=document.source_document_id,
            kind=document.kind,
            storage_key=storage_key,
            sha256=stored_sha256,
            size_bytes=stored_size,
        )

    def resolve(self, storage_key: str) -> Path:
        return self._resolve_storage_key(storage_key)

    def _resolve_storage_key(self, storage_key: str) -> Path:
        candidate = (self._root / storage_key).resolve()

        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise DocumentStorageError(
                f"unsafe storage key: {storage_key}"
            ) from exc

        return candidate

    def _atomic_write(
        self,
        *,
        target: Path,
        content: bytes,
    ) -> None:
        fd, temporary_path_str = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_path_str)

        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(temporary_path, target)
        except Exception:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0

    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)

    return digest.hexdigest(), size
