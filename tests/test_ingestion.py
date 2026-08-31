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
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    checkpoint_ingestion_run,
    complete_ingestion_run,
    fail_ingestion_run,
    pause_ingestion_run,
    register_ingestion_item,
    resume_ingestion_run,
    retry_failed_ingestion_item,
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



def test_pause_and_resume_ingestion_run() -> None:
    session = Mock()
    run = IngestionRun(status=RUN_STATUS_RUNNING)

    pause_ingestion_run(session, run)

    assert run.status == RUN_STATUS_PAUSED
    assert run.finished_at is None

    resume_ingestion_run(session, run)

    assert run.status == RUN_STATUS_RUNNING
    assert run.finished_at is None


def test_checkpoint_advances_running_run() -> None:
    session = Mock()
    run = IngestionRun(
        status=RUN_STATUS_RUNNING,
        last_completed_page=1,
    )

    checkpoint_ingestion_run(
        session,
        run,
        completed_page=2,
    )

    assert run.last_completed_page == 2
    assert isinstance(run.last_checkpoint_at, datetime)


def test_checkpoint_cannot_move_backwards() -> None:
    session = Mock()
    run = IngestionRun(
        status=RUN_STATUS_RUNNING,
        last_completed_page=3,
    )

    with pytest.raises(ValueError, match="cannot move backwards"):
        checkpoint_ingestion_run(
            session,
            run,
            completed_page=2,
        )


def test_start_run_can_persist_resume_metadata() -> None:
    session = Mock()

    run = start_ingestion_run(
        session,
        mode="batch",
        period_start="2026-08-01T00:00:00.000Z",
        period_end="2026-08-31T23:59:59.999Z",
        page_size=25,
    )

    assert run.mode == "batch"
    assert run.period_start == "2026-08-01T00:00:00.000Z"
    assert run.period_end == "2026-08-31T23:59:59.999Z"
    assert run.page_size == 25
    assert run.last_completed_page == 0



def test_retry_failed_item_reopens_same_item_and_increments_counter() -> None:
    session = Mock()
    item = IngestionItem(
        id=56,
        run_id=8,
        source_record_id="anvisa-product:4729",
        status=ITEM_STATUS_FAILED,
        error_code="AnvisaSourceError",
        error_message="ANVISA returned HTTP 500",
        error_class="transient",
        retry_count=0,
    )

    retry_failed_ingestion_item(session, item)

    assert item.id == 56
    assert item.status == ITEM_STATUS_FETCHING
    assert item.retry_count == 1
    assert item.error_code is None
    assert item.error_message is None
    assert item.error_class is None
    session.flush.assert_called_once_with()


def test_retry_rejects_non_failed_item() -> None:
    session = Mock()
    item = IngestionItem(
        id=56,
        run_id=8,
        source_record_id="anvisa-product:4729",
        status=ITEM_STATUS_READY,
        retry_count=0,
    )

    with pytest.raises(ValueError, match="only failed"):
        retry_failed_ingestion_item(session, item)
