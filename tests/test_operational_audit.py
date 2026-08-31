from pathlib import Path
from unittest.mock import Mock

import pytest

from bulario_service.operational_audit import (
    OperationalAuditError,
    run_operational_audit,
)
from bulario_service.portal_handoff import PortalHandoffReport


class ScalarResult:
    def __init__(self, value: int):
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class FakeSession:
    def __init__(self, counts: list[int]):
        self.counts = list(counts)
        self.sql: list[str] = []

    def execute(self, statement):
        self.sql.append(str(statement))
        return ScalarResult(self.counts.pop(0))


def handoff(row_id: int = 79) -> PortalHandoffReport:
    return PortalHandoffReport(
        public_row_id=row_id,
        source_record_id="anvisa:3606343:35480424",
        source_product_id=3606343,
        source_document_id=35480424,
        ingestion_status="ready",
        patient_storage_key=(
            "bulas/3606343/35480424/patient.pdf"
        ),
        professional_storage_key=(
            "bulas/3606343/35480424/professional.pdf"
        ),
        patient_sha256="a" * 64,
        professional_sha256="b" * 64,
    )


def test_operational_audit_accepts_closed_sprint_invariants(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = FakeSession([
        79,  # public rows
        0,   # duplicate source ids
        0,   # non-ready public rows
        0,   # incomplete public rows
        0,   # running runs
        0,   # failed incremental runs
        1,   # paused incremental runs
    ])
    reports = tuple(handoff(row_id=value) for value in range(1, 80))
    monkeypatch.setattr(
        "bulario_service.operational_audit.validate_all_ready_handoffs",
        lambda session, storage_root: reports,
    )

    report = run_operational_audit(
        session,
        storage_root=tmp_path,
    )

    assert report.ok is True
    assert report.public_anvisa_rows == 79
    assert report.validated_handoffs == 79
    assert report.latest_public_row_id == 79
    assert report.failed_incremental_runs == 0
    assert len(session.sql) == 7
    assert all("SELECT" in statement for statement in session.sql)
    assert all(
        keyword not in " ".join(session.sql).upper()
        for keyword in ("INSERT ", "UPDATE ", "DELETE ")
    )


def test_operational_audit_rejects_duplicate_public_identity(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = FakeSession([
        79,
        1,
        0,
        0,
        0,
        0,
        1,
    ])
    reports = tuple(handoff(row_id=value) for value in range(1, 80))
    monkeypatch.setattr(
        "bulario_service.operational_audit.validate_all_ready_handoffs",
        lambda session, storage_root: reports,
    )

    with pytest.raises(
        OperationalAuditError,
        match="duplicate_source_record_ids=1",
    ):
        run_operational_audit(
            session,
            storage_root=tmp_path,
        )


def test_operational_audit_rejects_unresolved_failed_incremental(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = FakeSession([
        79,
        0,
        0,
        0,
        0,
        1,
        0,
    ])
    reports = tuple(handoff(row_id=value) for value in range(1, 80))
    monkeypatch.setattr(
        "bulario_service.operational_audit.validate_all_ready_handoffs",
        lambda session, storage_root: reports,
    )

    with pytest.raises(
        OperationalAuditError,
        match="failed_incremental_runs=1",
    ):
        run_operational_audit(
            session,
            storage_root=tmp_path,
        )


def test_operational_audit_requires_all_public_handoffs_validated(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = FakeSession([
        79,
        0,
        0,
        0,
        0,
        0,
        1,
    ])
    reports = tuple(handoff(row_id=value) for value in range(1, 79))
    monkeypatch.setattr(
        "bulario_service.operational_audit.validate_all_ready_handoffs",
        lambda session, storage_root: reports,
    )

    with pytest.raises(
        OperationalAuditError,
        match="validated_handoffs=78/79",
    ):
        run_operational_audit(
            session,
            storage_root=tmp_path,
        )
