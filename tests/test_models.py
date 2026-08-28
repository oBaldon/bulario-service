from bulario_service.models import IngestionItem, IngestionRun


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
