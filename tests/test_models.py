from bulario_service.models import (
    BularioDocumentArtifact,
    BularioDocumentTextArtifact,
    BularioDocumentVersion,
    BularioProduct,
    IngestionItem,
    IngestionRun,
)


def test_ingestion_tables_belong_to_bulario_schema() -> None:
    assert IngestionRun.__table__.schema == "bulario"
    assert IngestionItem.__table__.schema == "bulario"


def test_ingestion_item_keeps_operational_payload_and_fingerprint() -> None:
    columns = IngestionItem.__table__.columns

    assert "raw_payload" in columns
    assert "normalized_payload" in columns
    assert "source_fingerprint" in columns
    assert columns["source_fingerprint"].type.length == 64


def test_source_record_is_unique_within_a_run() -> None:
    constraint_names = {
        constraint.name
        for constraint in IngestionItem.__table__.constraints
        if constraint.name is not None
    }

    assert "uq_ingestion_items_run_source_record" in constraint_names



def test_operational_document_tables_belong_to_bulario_schema() -> None:
    assert BularioProduct.__table__.schema == "bulario"
    assert BularioDocumentVersion.__table__.schema == "bulario"
    assert BularioDocumentArtifact.__table__.schema == "bulario"


def test_document_version_keeps_fingerprint_and_provenance_link() -> None:
    columns = BularioDocumentVersion.__table__.columns

    assert columns["source_fingerprint"].type.length == 64
    assert "last_ingestion_item_id" in columns
    assert "is_current" in columns


def test_document_artifact_has_version_kind_and_storage_uniqueness() -> None:
    constraint_names = {
        constraint.name
        for constraint in BularioDocumentArtifact.__table__.constraints
        if constraint.name is not None
    }

    assert "uq_bulario_document_artifacts_version_kind" in constraint_names
    assert "uq_bulario_document_artifacts_storage_key" in constraint_names



def test_document_text_artifact_belongs_to_bulario_schema() -> None:
    assert BularioDocumentTextArtifact.__table__.schema == "bulario"


def test_document_text_artifact_is_versioned_by_normalization() -> None:
    constraint_names = {
        constraint.name
        for constraint in BularioDocumentTextArtifact.__table__.constraints
        if constraint.name is not None
    }

    assert "uq_bulario_document_text_artifact_version" in constraint_names
    columns = BularioDocumentTextArtifact.__table__.columns
    assert columns["text_sha256"].type.length == 64
    assert "text_content" in columns
