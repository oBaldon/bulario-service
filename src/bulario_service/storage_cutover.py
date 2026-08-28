from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from bulario_service.models import BularioDocumentArtifact


class StorageCutoverError(RuntimeError):
    """Raised when the shared archive cannot be reconciled safely."""


@dataclass(frozen=True)
class StorageCutoverReport:
    total_artifacts: int
    copied: int
    pending_copy: int
    reused: int


def reconcile_shared_storage(
    session: Session,
    *,
    source_root: Path,
    target_root: Path,
    write: bool,
) -> StorageCutoverReport:
    source_root = source_root.resolve()
    target_root = target_root.resolve()

    artifacts = tuple(
        session.scalars(
            select(BularioDocumentArtifact)
            .order_by(BularioDocumentArtifact.id)
        )
    )

    copied = 0
    pending_copy = 0
    reused = 0

    for artifact in artifacts:
        source = _resolve_key(source_root, artifact.storage_key)
        target = _resolve_key(target_root, artifact.storage_key)

        if target.is_file():
            _assert_file_matches(
                target,
                expected_sha256=artifact.sha256,
                label="target",
            )
            reused += 1
            continue

        if not source.is_file():
            raise StorageCutoverError(
                "document artifact is missing from both source and target "
                f"storage_key={artifact.storage_key}"
            )

        _assert_file_matches(
            source,
            expected_sha256=artifact.sha256,
            label="source",
        )

        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_copy(source=source, target=target)
            _assert_file_matches(
                target,
                expected_sha256=artifact.sha256,
                label="target",
            )
            copied += 1
        else:
            pending_copy += 1

    return StorageCutoverReport(
        total_artifacts=len(artifacts),
        copied=copied,
        pending_copy=pending_copy,
        reused=reused,
    )


def _resolve_key(root: Path, storage_key: str) -> Path:
    candidate = (root / storage_key).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise StorageCutoverError(
            f"unsafe storage key: {storage_key}"
        ) from exc
    return candidate


def _assert_file_matches(
    path: Path,
    *,
    expected_sha256: str,
    label: str,
) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        header = handle.read(5)
        if header != b"%PDF-":
            raise StorageCutoverError(
                f"{label} file is not a PDF path={path}"
            )
        digest.update(header)
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)

    actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected_sha256:
        raise StorageCutoverError(
            f"{label} file hash mismatch path={path} "
            f"expected={expected_sha256} actual={actual_sha256}"
        )


def _atomic_copy(*, source: Path, target: Path) -> None:
    fd, temporary_path_str = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_path_str)

    try:
        with source.open("rb") as source_handle, os.fdopen(
            fd,
            "wb",
        ) as target_handle:
            shutil.copyfileobj(source_handle, target_handle)
            target_handle.flush()
            os.fsync(target_handle.fileno())

        os.replace(temporary_path, target)
    except Exception:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise
