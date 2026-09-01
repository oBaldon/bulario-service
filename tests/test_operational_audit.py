import hashlib
from pathlib import Path

import pytest

from bulario_service.operational_audit import (
    DocumentArtifactAudit,
    OperationalAuditError,
    _audit_document_artifacts,
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


class MappingResult:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class ArtifactSession:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.sql: list[str] = []

    def execute(self, statement):
        self.sql.append(str(statement))
        return MappingResult(self.rows)


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


def healthy_counts() -> list[int]:
    return [
        79,  # public rows
        0,   # duplicate source ids
        0,   # non-ready public rows
        0,   # incomplete public rows
        0,   # running runs
        0,   # failed incremental runs
        1,   # paused incremental runs
        78,  # operational products
        84,  # document versions
        6,   # historical versions
        1,   # products with multiple versions
        0,   # products with invalid current count
        0,   # versions without artifacts
    ]


def healthy_artifact_audit() -> DocumentArtifactAudit:
    return DocumentArtifactAudit(
        audited_document_artifacts=168,
        invalid_artifact_relationships=0,
        missing_physical_artifacts=0,
        artifact_hash_mismatches=0,
        artifacts_without_text_v1=0,
    )


def install_healthy_external_audits(monkeypatch) -> None:
    reports = tuple(handoff(row_id=value) for value in range(1, 80))
    monkeypatch.setattr(
        "bulario_service.operational_audit.validate_all_ready_handoffs",
        lambda session, storage_root: reports,
    )
    monkeypatch.setattr(
        "bulario_service.operational_audit._audit_document_artifacts",
        lambda session, storage_root: healthy_artifact_audit(),
    )


def test_operational_audit_accepts_historical_invariants(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = FakeSession(healthy_counts())
    install_healthy_external_audits(monkeypatch)

    report = run_operational_audit(
        session,
        storage_root=tmp_path,
    )

    assert report.ok is True
    assert report.public_anvisa_rows == 79
    assert report.validated_handoffs == 79
    assert report.latest_public_row_id == 79
    assert report.failed_incremental_runs == 0
    assert report.operational_products == 78
    assert report.document_versions == 84
    assert report.historical_versions == 6
    assert report.products_with_multiple_versions == 1
    assert report.invalid_current_version_products == 0
    assert report.versions_without_artifacts == 0
    assert report.audited_document_artifacts == 168
    assert len(session.sql) == 13
    assert all("SELECT" in statement for statement in session.sql)
    assert all(
        keyword not in " ".join(session.sql).upper()
        for keyword in ("INSERT ", "UPDATE ", "DELETE ")
    )


def test_operational_audit_rejects_duplicate_public_identity(
    monkeypatch,
    tmp_path: Path,
) -> None:
    counts = healthy_counts()
    counts[1] = 1
    session = FakeSession(counts)
    install_healthy_external_audits(monkeypatch)

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
    counts = healthy_counts()
    counts[5] = 1
    counts[6] = 0
    session = FakeSession(counts)
    install_healthy_external_audits(monkeypatch)

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
    session = FakeSession(healthy_counts())
    reports = tuple(handoff(row_id=value) for value in range(1, 79))
    monkeypatch.setattr(
        "bulario_service.operational_audit.validate_all_ready_handoffs",
        lambda session, storage_root: reports,
    )
    monkeypatch.setattr(
        "bulario_service.operational_audit._audit_document_artifacts",
        lambda session, storage_root: healthy_artifact_audit(),
    )

    with pytest.raises(
        OperationalAuditError,
        match="validated_handoffs=78/79",
    ):
        run_operational_audit(
            session,
            storage_root=tmp_path,
        )


def test_operational_audit_rejects_invalid_current_version_count(
    monkeypatch,
    tmp_path: Path,
) -> None:
    counts = healthy_counts()
    counts[11] = 2
    session = FakeSession(counts)
    install_healthy_external_audits(monkeypatch)

    with pytest.raises(
        OperationalAuditError,
        match="invalid_current_version_products=2",
    ):
        run_operational_audit(
            session,
            storage_root=tmp_path,
        )


def test_operational_audit_rejects_versions_without_artifacts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    counts = healthy_counts()
    counts[12] = 1
    session = FakeSession(counts)
    install_healthy_external_audits(monkeypatch)

    with pytest.raises(
        OperationalAuditError,
        match="versions_without_artifacts=1",
    ):
        run_operational_audit(
            session,
            storage_root=tmp_path,
        )


def test_document_artifact_audit_validates_history_file_hash_and_text(
    tmp_path: Path,
) -> None:
    storage_key = "bulas/524068/24622459/patient.pdf"
    content = b"%PDF-historical-patient"
    target = tmp_path / storage_key
    target.parent.mkdir(parents=True)
    target.write_bytes(content)

    session = ArtifactSession([
        {
            "source_product_id": 524068,
            "source_document_id": 24622459,
            "kind": "patient",
            "storage_key": storage_key,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "has_text_v1": True,
        }
    ])

    report = _audit_document_artifacts(
        session,
        storage_root=tmp_path,
    )

    assert report == DocumentArtifactAudit(
        audited_document_artifacts=1,
        invalid_artifact_relationships=0,
        missing_physical_artifacts=0,
        artifact_hash_mismatches=0,
        artifacts_without_text_v1=0,
    )
    assert len(session.sql) == 1
    assert "document_text_artifacts" in session.sql[0]
    assert "SELECT" in session.sql[0].upper()


def test_document_artifact_audit_reports_all_integrity_failures(
    tmp_path: Path,
) -> None:
    valid_key = "bulas/524068/29015111/professional.pdf"
    bad_hash_content = b"%PDF-wrong-bytes"
    target = tmp_path / valid_key
    target.parent.mkdir(parents=True)
    target.write_bytes(bad_hash_content)

    session = ArtifactSession([
        {
            "source_product_id": 524068,
            "source_document_id": 24622459,
            "kind": "patient",
            "storage_key": "bulas/524068/999/patient.pdf",
            "sha256": "a" * 64,
            "size_bytes": 10,
            "has_text_v1": True,
        },
        {
            "source_product_id": 524068,
            "source_document_id": 9683484,
            "kind": "patient",
            "storage_key": "bulas/524068/9683484/patient.pdf",
            "sha256": "b" * 64,
            "size_bytes": 10,
            "has_text_v1": False,
        },
        {
            "source_product_id": 524068,
            "source_document_id": 29015111,
            "kind": "professional",
            "storage_key": valid_key,
            "sha256": "c" * 64,
            "size_bytes": len(bad_hash_content),
            "has_text_v1": True,
        },
    ])

    report = _audit_document_artifacts(
        session,
        storage_root=tmp_path,
    )

    assert report.audited_document_artifacts == 3
    assert report.invalid_artifact_relationships == 1
    assert report.missing_physical_artifacts == 1
    assert report.artifact_hash_mismatches == 1
    assert report.artifacts_without_text_v1 == 1
