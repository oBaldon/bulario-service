from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath

from sqlalchemy import text
from sqlalchemy.orm import Session

from bulario_service.config import load_settings
from bulario_service.db import create_database_engine
from bulario_service.portal_handoff import (
    PortalHandoffError,
    PortalHandoffReport,
    validate_all_ready_handoffs,
)


class OperationalAuditError(RuntimeError):
    """Raised when Sprint 02 operational invariants are not satisfied."""


@dataclass(frozen=True)
class OperationalAuditReport:
    public_anvisa_rows: int
    duplicate_source_record_ids: int
    non_ready_public_rows: int
    incomplete_public_rows: int
    running_runs: int
    failed_incremental_runs: int
    paused_incremental_runs: int
    validated_handoffs: int
    latest_public_row_id: int
    latest_source_record_id: str
    latest_patient_storage_key: str
    latest_professional_storage_key: str
    latest_handoff_ready: bool
    operational_products: int
    document_versions: int
    historical_versions: int
    products_with_multiple_versions: int
    invalid_current_version_products: int
    versions_without_artifacts: int
    audited_document_artifacts: int
    invalid_artifact_relationships: int
    missing_physical_artifacts: int
    artifact_hash_mismatches: int
    artifacts_without_text_v1: int

    @property
    def ok(self) -> bool:
        return (
            self.public_anvisa_rows > 0
            and self.duplicate_source_record_ids == 0
            and self.non_ready_public_rows == 0
            and self.incomplete_public_rows == 0
            and self.running_runs == 0
            and self.failed_incremental_runs == 0
            and self.paused_incremental_runs <= 1
            and self.validated_handoffs == self.public_anvisa_rows
            and self.latest_handoff_ready
            and self.operational_products > 0
            and self.document_versions >= self.operational_products
            and self.invalid_current_version_products == 0
            and self.versions_without_artifacts == 0
            and self.invalid_artifact_relationships == 0
            and self.missing_physical_artifacts == 0
            and self.artifact_hash_mismatches == 0
            and self.artifacts_without_text_v1 == 0
        )


def run_operational_audit(
    session: Session,
    *,
    storage_root: Path,
) -> OperationalAuditReport:
    public_anvisa_rows = _count(
        session,
        """
        SELECT COUNT(*)
        FROM public.bulas
        WHERE source_record_id LIKE 'anvisa:%'
        """,
    )
    duplicate_source_record_ids = _count(
        session,
        """
        SELECT COUNT(*)
        FROM (
            SELECT source_record_id
            FROM public.bulas
            WHERE source_record_id LIKE 'anvisa:%'
            GROUP BY source_record_id
            HAVING COUNT(*) > 1
        ) duplicated
        """,
    )
    non_ready_public_rows = _count(
        session,
        """
        SELECT COUNT(*)
        FROM public.bulas
        WHERE source_record_id LIKE 'anvisa:%'
          AND ingestion_status IS DISTINCT FROM 'ready'
        """,
    )
    incomplete_public_rows = _count(
        session,
        """
        SELECT COUNT(*)
        FROM public.bulas
        WHERE source_record_id LIKE 'anvisa:%'
          AND (
              source_fingerprint IS NULL
              OR source_fingerprint = ''
              OR bula_paciente IS NULL
              OR bula_paciente = ''
              OR bula_profissional IS NULL
              OR bula_profissional = ''
              OR bula_paciente_sha256 IS NULL
              OR char_length(bula_paciente_sha256) <> 64
              OR bula_profissional_sha256 IS NULL
              OR char_length(bula_profissional_sha256) <> 64
          )
        """,
    )
    running_runs = _count(
        session,
        """
        SELECT COUNT(*)
        FROM bulario.ingestion_runs
        WHERE status = 'running'
        """,
    )
    failed_incremental_runs = _count(
        session,
        """
        SELECT COUNT(*)
        FROM bulario.ingestion_runs
        WHERE mode = 'incremental'
          AND status = 'failed'
        """,
    )
    paused_incremental_runs = _count(
        session,
        """
        SELECT COUNT(*)
        FROM bulario.ingestion_runs
        WHERE mode = 'incremental'
          AND status = 'paused'
        """,
    )

    operational_products = _count(
        session,
        """
        SELECT COUNT(*)
        FROM bulario.products
        """,
    )
    document_versions = _count(
        session,
        """
        SELECT COUNT(*)
        FROM bulario.document_versions
        """,
    )
    historical_versions = _count(
        session,
        """
        SELECT COUNT(*)
        FROM bulario.document_versions
        WHERE is_current IS FALSE
        """,
    )
    products_with_multiple_versions = _count(
        session,
        """
        SELECT COUNT(*)
        FROM (
            SELECT product_id
            FROM bulario.document_versions
            GROUP BY product_id
            HAVING COUNT(*) > 1
        ) versioned_products
        """,
    )
    invalid_current_version_products = _count(
        session,
        """
        SELECT COUNT(*)
        FROM (
            SELECT
                p.id
            FROM bulario.products p
            LEFT JOIN bulario.document_versions dv
              ON dv.product_id = p.id
            GROUP BY p.id
            HAVING COUNT(dv.id) = 0
                OR COUNT(*) FILTER (WHERE dv.is_current) <> 1
        ) invalid_current
        """,
    )
    versions_without_artifacts = _count(
        session,
        """
        SELECT COUNT(*)
        FROM (
            SELECT dv.id
            FROM bulario.document_versions dv
            LEFT JOIN bulario.document_artifacts da
              ON da.document_version_id = dv.id
            GROUP BY dv.id
            HAVING COUNT(da.id) = 0
        ) versions_without_artifacts
        """,
    )

    artifact_audit = _audit_document_artifacts(
        session,
        storage_root=storage_root,
    )

    handoffs = validate_all_ready_handoffs(
        session,
        storage_root=storage_root,
    )
    handoff = handoffs[-1]

    report = OperationalAuditReport(
        public_anvisa_rows=public_anvisa_rows,
        duplicate_source_record_ids=duplicate_source_record_ids,
        non_ready_public_rows=non_ready_public_rows,
        incomplete_public_rows=incomplete_public_rows,
        running_runs=running_runs,
        failed_incremental_runs=failed_incremental_runs,
        paused_incremental_runs=paused_incremental_runs,
        validated_handoffs=len(handoffs),
        latest_public_row_id=handoff.public_row_id,
        latest_source_record_id=handoff.source_record_id,
        latest_patient_storage_key=handoff.patient_storage_key,
        latest_professional_storage_key=handoff.professional_storage_key,
        latest_handoff_ready=True,
        operational_products=operational_products,
        document_versions=document_versions,
        historical_versions=historical_versions,
        products_with_multiple_versions=products_with_multiple_versions,
        invalid_current_version_products=invalid_current_version_products,
        versions_without_artifacts=versions_without_artifacts,
        audited_document_artifacts=artifact_audit.audited_document_artifacts,
        invalid_artifact_relationships=artifact_audit.invalid_artifact_relationships,
        missing_physical_artifacts=artifact_audit.missing_physical_artifacts,
        artifact_hash_mismatches=artifact_audit.artifact_hash_mismatches,
        artifacts_without_text_v1=artifact_audit.artifacts_without_text_v1,
    )

    if not report.ok:
        raise OperationalAuditError(_failure_summary(report))

    return report


def main() -> None:
    settings = load_settings()
    engine = create_database_engine(settings)
    try:
        with Session(engine) as session:
            try:
                report = run_operational_audit(
                    session,
                    storage_root=settings.storage_root,
                )
            except (OperationalAuditError, PortalHandoffError) as exc:
                print(
                    json.dumps(
                        {
                            "event": "operational_audit",
                            "ok": False,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
                raise SystemExit(2) from exc

            payload = asdict(report)
            payload["event"] = "operational_audit"
            payload["ok"] = report.ok
            print(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            print("sprint02_operational_audit_ready=true")
    finally:
        engine.dispose()


def _count(session: Session, sql: str) -> int:
    value = session.execute(text(sql)).scalar_one()
    return int(value)


@dataclass(frozen=True)
class DocumentArtifactAudit:
    audited_document_artifacts: int
    invalid_artifact_relationships: int
    missing_physical_artifacts: int
    artifact_hash_mismatches: int
    artifacts_without_text_v1: int


def _audit_document_artifacts(
    session: Session,
    *,
    storage_root: Path,
) -> DocumentArtifactAudit:
    rows = session.execute(
        text(
            """
            SELECT
                p.source_product_id,
                dv.source_document_id,
                da.kind,
                da.storage_key,
                da.sha256,
                da.size_bytes,
                EXISTS (
                    SELECT 1
                    FROM bulario.document_text_artifacts dta
                    WHERE dta.document_artifact_id = da.id
                      AND dta.normalization_version = 'v1'
                ) AS has_text_v1
            FROM bulario.document_artifacts da
            JOIN bulario.document_versions dv
              ON dv.id = da.document_version_id
            JOIN bulario.products p
              ON p.id = dv.product_id
            ORDER BY
                p.source_product_id,
                dv.source_document_id,
                da.kind
            """
        )
    ).mappings().all()

    root = storage_root.resolve()
    invalid_relationships = 0
    missing_files = 0
    hash_mismatches = 0
    missing_text = 0

    for row in rows:
        kind = str(row["kind"])
        storage_key = str(row["storage_key"])
        expected_key = (
            f"bulas/{int(row['source_product_id'])}/"
            f"{int(row['source_document_id'])}/{kind}.pdf"
        )

        if (
            kind not in {"patient", "professional"}
            or storage_key != expected_key
            or not _safe_pdf_storage_key(storage_key)
        ):
            invalid_relationships += 1
            continue

        if not bool(row["has_text_v1"]):
            missing_text += 1

        file_path = _resolve_storage_key(
            root=root,
            storage_key=storage_key,
        )
        if file_path is None or not file_path.is_file():
            missing_files += 1
            continue

        digest, size = _hash_file(file_path)
        if (
            digest != str(row["sha256"])
            or size != int(row["size_bytes"])
        ):
            hash_mismatches += 1

    return DocumentArtifactAudit(
        audited_document_artifacts=len(rows),
        invalid_artifact_relationships=invalid_relationships,
        missing_physical_artifacts=missing_files,
        artifact_hash_mismatches=hash_mismatches,
        artifacts_without_text_v1=missing_text,
    )


def _safe_pdf_storage_key(storage_key: str) -> bool:
    relative = PurePosixPath(storage_key)
    return (
        not relative.is_absolute()
        and ".." not in relative.parts
        and relative.suffix.lower() == ".pdf"
    )


def _resolve_storage_key(
    *,
    root: Path,
    storage_key: str,
) -> Path | None:
    relative = PurePosixPath(storage_key)
    candidate = (root / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _failure_summary(report: OperationalAuditReport) -> str:
    failures: list[str] = []
    if report.public_anvisa_rows < 1:
        failures.append("no ANVISA public rows")
    if report.duplicate_source_record_ids:
        failures.append(
            f"duplicate_source_record_ids={report.duplicate_source_record_ids}"
        )
    if report.non_ready_public_rows:
        failures.append(
            f"non_ready_public_rows={report.non_ready_public_rows}"
        )
    if report.incomplete_public_rows:
        failures.append(
            f"incomplete_public_rows={report.incomplete_public_rows}"
        )
    if report.running_runs:
        failures.append(f"running_runs={report.running_runs}")
    if report.failed_incremental_runs:
        failures.append(
            f"failed_incremental_runs={report.failed_incremental_runs}"
        )
    if report.paused_incremental_runs > 1:
        failures.append(
            f"paused_incremental_runs={report.paused_incremental_runs}"
        )
    if report.validated_handoffs != report.public_anvisa_rows:
        failures.append(
            "validated_handoffs="
            f"{report.validated_handoffs}/{report.public_anvisa_rows}"
        )
    if not report.latest_handoff_ready:
        failures.append("latest_handoff_ready=false")
    if report.operational_products < 1:
        failures.append("no operational products")
    if report.document_versions < report.operational_products:
        failures.append(
            "document_versions="
            f"{report.document_versions}/{report.operational_products}"
        )
    if report.invalid_current_version_products:
        failures.append(
            "invalid_current_version_products="
            f"{report.invalid_current_version_products}"
        )
    if report.versions_without_artifacts:
        failures.append(
            f"versions_without_artifacts={report.versions_without_artifacts}"
        )
    if report.invalid_artifact_relationships:
        failures.append(
            "invalid_artifact_relationships="
            f"{report.invalid_artifact_relationships}"
        )
    if report.missing_physical_artifacts:
        failures.append(
            "missing_physical_artifacts="
            f"{report.missing_physical_artifacts}"
        )
    if report.artifact_hash_mismatches:
        failures.append(
            f"artifact_hash_mismatches={report.artifact_hash_mismatches}"
        )
    if report.artifacts_without_text_v1:
        failures.append(
            f"artifacts_without_text_v1={report.artifacts_without_text_v1}"
        )
    return "operational audit failed: " + ", ".join(failures)


if __name__ == "__main__":
    main()
