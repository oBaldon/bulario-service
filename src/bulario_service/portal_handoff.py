from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from bulario_service.models import (
    BularioDocumentArtifact,
    BularioDocumentTextArtifact,
    BularioDocumentVersion,
    BularioProduct,
)


class PortalHandoffError(RuntimeError):
    """Raised when a ready public row cannot be handed off safely."""


@dataclass(frozen=True)
class PortalHandoffReport:
    public_row_id: int
    source_record_id: str
    source_product_id: int
    source_document_id: int
    ingestion_status: str
    patient_storage_key: str
    professional_storage_key: str
    patient_sha256: str
    professional_sha256: str


def validate_latest_ready_handoff(
    session: Session,
    *,
    storage_root: Path,
) -> PortalHandoffReport:
    row = session.execute(
        text(
            """
            SELECT
                id,
                medicamento,
                source_record_id,
                source_fingerprint,
                ingestion_status,
                bula_paciente,
                bula_profissional,
                bula_paciente_sha256,
                bula_profissional_sha256
            FROM public.bulas
            WHERE ingestion_status = 'ready'
              AND source_record_id LIKE 'anvisa:%'
            ORDER BY id DESC
            LIMIT 1
            """
        )
    ).mappings().first()

    if row is None:
        raise PortalHandoffError("no ready ANVISA public.bulas row was found")

    return _validate_ready_handoff_row(
        session,
        row=row,
        storage_root=storage_root,
    )


def validate_all_ready_handoffs(
    session: Session,
    *,
    storage_root: Path,
) -> tuple[PortalHandoffReport, ...]:
    rows = tuple(
        session.execute(
            text(
                """
                SELECT
                    id,
                    medicamento,
                    source_record_id,
                    source_fingerprint,
                    ingestion_status,
                    bula_paciente,
                    bula_profissional,
                    bula_paciente_sha256,
                    bula_profissional_sha256
                FROM public.bulas
                WHERE ingestion_status = 'ready'
                  AND source_record_id LIKE 'anvisa:%'
                ORDER BY id ASC
                """
            )
        ).mappings()
    )

    if not rows:
        raise PortalHandoffError("no ready ANVISA public.bulas rows were found")

    return tuple(
        _validate_ready_handoff_row(
            session,
            row=row,
            storage_root=storage_root,
        )
        for row in rows
    )


def _validate_ready_handoff_row(
    session: Session,
    *,
    row,
    storage_root: Path,
) -> PortalHandoffReport:
    source_product_id, source_document_id = _parse_source_record_id(
        row["source_record_id"]
    )

    version = session.scalar(
        select(BularioDocumentVersion)
        .join(
            BularioProduct,
            BularioProduct.id == BularioDocumentVersion.product_id,
        )
        .where(
            BularioProduct.source_product_id == source_product_id,
            BularioDocumentVersion.source_document_id == source_document_id,
        )
    )
    if version is None:
        raise PortalHandoffError(
            "ready public row has no matching operational document version"
        )

    if version.source_fingerprint != row["source_fingerprint"]:
        raise PortalHandoffError(
            "public source_fingerprint differs from operational version"
        )

    artifacts = tuple(
        session.scalars(
            select(BularioDocumentArtifact).where(
                BularioDocumentArtifact.document_version_id == version.id
            )
        )
    )
    by_kind = {artifact.kind: artifact for artifact in artifacts}
    if set(by_kind) != {"patient", "professional"}:
        raise PortalHandoffError(
            "operational version must have exactly patient and professional PDFs"
        )

    _validate_document_handoff(
        storage_root=storage_root,
        public_storage_key=row["bula_paciente"],
        public_sha256=row["bula_paciente_sha256"],
        operational_artifact=by_kind["patient"],
        label="patient",
    )
    _validate_document_handoff(
        storage_root=storage_root,
        public_storage_key=row["bula_profissional"],
        public_sha256=row["bula_profissional_sha256"],
        operational_artifact=by_kind["professional"],
        label="professional",
    )

    _validate_text_handoff(
        session,
        operational_artifact=by_kind["patient"],
        label="patient",
    )
    _validate_text_handoff(
        session,
        operational_artifact=by_kind["professional"],
        label="professional",
    )

    return PortalHandoffReport(
        public_row_id=row["id"],
        source_record_id=row["source_record_id"],
        source_product_id=source_product_id,
        source_document_id=source_document_id,
        ingestion_status=row["ingestion_status"],
        patient_storage_key=row["bula_paciente"],
        professional_storage_key=row["bula_profissional"],
        patient_sha256=row["bula_paciente_sha256"],
        professional_sha256=row["bula_profissional_sha256"],
    )


def _validate_text_handoff(
    session: Session,
    *,
    operational_artifact: BularioDocumentArtifact,
    label: str,
) -> None:
    text_artifact = session.scalar(
        select(BularioDocumentTextArtifact).where(
            BularioDocumentTextArtifact.document_artifact_id
            == operational_artifact.id,
            BularioDocumentTextArtifact.normalization_version == "v1",
        )
    )
    if text_artifact is None:
        raise PortalHandoffError(
            f"{label} operational PDF has no normalized text v1"
        )

    text_content = text_artifact.text_content
    if not text_content:
        raise PortalHandoffError(
            f"{label} normalized text v1 is empty"
        )
    if text_artifact.character_count != len(text_content):
        raise PortalHandoffError(
            f"{label} normalized text character_count is inconsistent"
        )

    text_sha256 = hashlib.sha256(
        text_content.encode("utf-8")
    ).hexdigest()
    if text_sha256 != text_artifact.text_sha256:
        raise PortalHandoffError(
            f"{label} normalized text SHA-256 is inconsistent"
        )


def _parse_source_record_id(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "anvisa":
        raise PortalHandoffError(
            f"unsupported source_record_id format: {value}"
        )
    try:
        source_product_id = int(parts[1])
        source_document_id = int(parts[2])
    except ValueError as exc:
        raise PortalHandoffError(
            f"invalid source_record_id numeric identities: {value}"
        ) from exc
    if source_product_id < 1 or source_document_id < 1:
        raise PortalHandoffError(
            f"source_record_id identities must be positive: {value}"
        )
    return source_product_id, source_document_id


def _validate_document_handoff(
    *,
    storage_root: Path,
    public_storage_key: str | None,
    public_sha256: str | None,
    operational_artifact: BularioDocumentArtifact,
    label: str,
) -> None:
    if not public_storage_key:
        raise PortalHandoffError(f"{label} public storage key is missing")
    if not public_sha256:
        raise PortalHandoffError(f"{label} public SHA-256 is missing")

    if public_storage_key != operational_artifact.storage_key:
        raise PortalHandoffError(
            f"{label} public storage key differs from operational artifact"
        )
    if public_sha256 != operational_artifact.sha256:
        raise PortalHandoffError(
            f"{label} public SHA-256 differs from operational artifact"
        )

    relative = PurePosixPath(public_storage_key)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.suffix.lower() != ".pdf"
    ):
        raise PortalHandoffError(f"{label} public storage key is unsafe")

    root = storage_root.resolve()
    file_path = (root / Path(*relative.parts)).resolve()
    try:
        file_path.relative_to(root)
    except ValueError as exc:
        raise PortalHandoffError(
            f"{label} public storage key escapes storage root"
        ) from exc

    if not file_path.is_file():
        raise PortalHandoffError(
            f"{label} PDF file does not exist: {public_storage_key}"
        )

    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        header = handle.read(5)
        if header != b"%PDF-":
            raise PortalHandoffError(f"{label} stored file is not a PDF")
        digest.update(header)
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)

    if digest.hexdigest() != public_sha256:
        raise PortalHandoffError(
            f"{label} stored file SHA-256 differs from public contract"
        )
