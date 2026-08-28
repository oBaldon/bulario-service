from datetime import datetime
from unittest.mock import Mock

import pytest

from bulario_service.ingestion import (
    ITEM_STATUS_DISCOVERED,
    ITEM_STATUS_DOWNLOADED,
    ITEM_STATUS_FAILED,
    ITEM_STATUS_FETCHING,
    ITEM_STATUS_NORMALIZED,
    ITEM_STATUS_READY,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    complete_ingestion_run,
    fail_ingestion_run,
    register_ingestion_item,
    start_ingestion_run,
    transition_ingestion_item,
)
from bulario_service.models import IngestionItem, IngestionRun


def test_start_ingestion_run_persists_running_run() -> None:
    session = Mock()

    run = start_ingestion_run(session)

    assert run.status == RUN_STATUS_RUNNING
    assert run.finished_at is None
    session.add.assert_called_once_with(run)
    session.flush.assert_called_once_with()


def test_complete_ingestion_run_sets_terminal_state_and_timestamp() -> None:
    session = Mock()
    run = IngestionRun(status=RUN_STATUS_RUNNING)

    complete_ingestion_run(session, run)

    assert run.status == RUN_STATUS_COMPLETED
    assert isinstance(run.finished_at, datetime)
    session.flush.assert_called_once_with()


def test_fail_ingestion_run_sets_terminal_state() -> None:
    session = Mock()
    run = IngestionRun(status=RUN_STATUS_RUNNING)

    fail_ingestion_run(session, run)

    assert run.status == RUN_STATUS_FAILED
    assert isinstance(run.finished_at, datetime)


def test_finished_run_cannot_be_finished_again() -> None:
    session = Mock()
    run = IngestionRun(status=RUN_STATUS_COMPLETED)

    with pytest.raises(ValueError, match="can only finish"):
        fail_ingestion_run(session, run)

    session.flush.assert_not_called()


def test_register_item_starts_as_discovered() -> None:
    session = Mock()
    run = IngestionRun(id=42, status=RUN_STATUS_RUNNING)

    item = register_ingestion_item(
        session,
        run,
        source_record_id="source-123",
        source_url="https://example.test/bula/123",
        raw_payload={"registro": "123"},
    )

    assert item.run_id == 42
    assert item.source_record_id == "source-123"
    assert item.status == ITEM_STATUS_DISCOVERED
    assert item.raw_payload == {"registro": "123"}
    session.add.assert_called_once_with(item)
    session.flush.assert_called_once_with()


def test_item_cannot_be_registered_before_run_is_persisted() -> None:
    session = Mock()
    run = IngestionRun(status=RUN_STATUS_RUNNING)

    with pytest.raises(ValueError, match="must be persisted"):
        register_ingestion_item(
            session,
            run,
            source_record_id="source-123",
        )

    session.add.assert_not_called()


def test_item_cannot_be_registered_in_finished_run() -> None:
    session = Mock()
    run = IngestionRun(id=42, status=RUN_STATUS_COMPLETED)

    with pytest.raises(ValueError, match="running ingestion run"):
        register_ingestion_item(
            session,
            run,
            source_record_id="source-123",
        )


def test_item_follows_happy_path_transitions() -> None:
    session = Mock()
    item = IngestionItem(
        run_id=42,
        source_record_id="source-123",
        status=ITEM_STATUS_DISCOVERED,
    )

    for status in (
        ITEM_STATUS_FETCHING,
        ITEM_STATUS_DOWNLOADED,
        ITEM_STATUS_NORMALIZED,
        ITEM_STATUS_READY,
    ):
        transition_ingestion_item(session, item, to_status=status)
        assert item.status == status

    assert session.flush.call_count == 4


def test_invalid_item_transition_is_rejected() -> None:
    session = Mock()
    item = IngestionItem(
        run_id=42,
        source_record_id="source-123",
        status=ITEM_STATUS_DISCOVERED,
    )

    with pytest.raises(ValueError, match="invalid ingestion item transition"):
        transition_ingestion_item(session, item, to_status=ITEM_STATUS_READY)

    assert item.status == ITEM_STATUS_DISCOVERED
    session.flush.assert_not_called()


def test_failed_item_requires_error_code_and_records_error() -> None:
    session = Mock()
    item = IngestionItem(
        run_id=42,
        source_record_id="source-123",
        status=ITEM_STATUS_FETCHING,
    )

    with pytest.raises(ValueError, match="error_code is required"):
        transition_ingestion_item(session, item, to_status=ITEM_STATUS_FAILED)

    transition_ingestion_item(
        session,
        item,
        to_status=ITEM_STATUS_FAILED,
        error_code="source_timeout",
        error_message="source request timed out",
    )

    assert item.status == ITEM_STATUS_FAILED
    assert item.error_code == "source_timeout"
    assert item.error_message == "source request timed out"


def test_error_details_are_rejected_outside_failed_transition() -> None:
    session = Mock()
    item = IngestionItem(
        run_id=42,
        source_record_id="source-123",
        status=ITEM_STATUS_DISCOVERED,
    )

    with pytest.raises(ValueError, match="only allowed for failed items"):
        transition_ingestion_item(
            session,
            item,
            to_status=ITEM_STATUS_FETCHING,
            error_code="unexpected",
        )
