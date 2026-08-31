from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

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
    return "operational audit failed: " + ", ".join(failures)


if __name__ == "__main__":
    main()
