from datetime import UTC, datetime

from sqlalchemy.orm import Session

from bulario_service.models import IngestionItem, IngestionRun


RUN_STATUS_RUNNING = "running"
RUN_STATUS_PAUSED = "paused"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"

ITEM_STATUS_DISCOVERED = "discovered"
ITEM_STATUS_FETCHING = "fetching"
ITEM_STATUS_DOWNLOADED = "downloaded"
ITEM_STATUS_NORMALIZED = "normalized"
ITEM_STATUS_READY = "ready"
ITEM_STATUS_FAILED = "failed"


_ITEM_TRANSITIONS: dict[str, set[str]] = {
    ITEM_STATUS_DISCOVERED: {ITEM_STATUS_FETCHING, ITEM_STATUS_FAILED},
    ITEM_STATUS_FETCHING: {ITEM_STATUS_DOWNLOADED, ITEM_STATUS_FAILED},
    ITEM_STATUS_DOWNLOADED: {ITEM_STATUS_NORMALIZED, ITEM_STATUS_FAILED},
    ITEM_STATUS_NORMALIZED: {ITEM_STATUS_READY, ITEM_STATUS_FAILED},
    ITEM_STATUS_READY: set(),
    ITEM_STATUS_FAILED: set(),
}


def start_ingestion_run(
    session: Session,
    *,
    mode: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    page_size: int | None = None,
) -> IngestionRun:
    run = IngestionRun(
        status=RUN_STATUS_RUNNING,
        mode=mode,
        period_start=period_start,
        period_end=period_end,
        page_size=page_size,
        last_completed_page=0,
    )
    session.add(run)
    session.flush()
    return run



def pause_ingestion_run(session: Session, run: IngestionRun) -> None:
    if run.status != RUN_STATUS_RUNNING:
        raise ValueError(
            "ingestion run can only pause from "
            f"'{RUN_STATUS_RUNNING}', current status is '{run.status}'"
        )

    run.status = RUN_STATUS_PAUSED
    session.flush()


def resume_ingestion_run(session: Session, run: IngestionRun) -> None:
    if run.status != RUN_STATUS_PAUSED:
        raise ValueError(
            "ingestion run can only resume from "
            f"'{RUN_STATUS_PAUSED}', current status is '{run.status}'"
        )

    run.status = RUN_STATUS_RUNNING
    session.flush()


def checkpoint_ingestion_run(
    session: Session,
    run: IngestionRun,
    *,
    completed_page: int,
) -> None:
    if run.status != RUN_STATUS_RUNNING:
        raise ValueError("checkpoint requires a running ingestion run")
    if completed_page < 1:
        raise ValueError("completed_page must be greater than zero")
    if completed_page < run.last_completed_page:
        raise ValueError("checkpoint cannot move backwards")

    run.last_completed_page = completed_page
    run.last_checkpoint_at = datetime.now(UTC)
    session.flush()

def complete_ingestion_run(session: Session, run: IngestionRun) -> None:
    _finish_ingestion_run(session, run, RUN_STATUS_COMPLETED)


def fail_ingestion_run(session: Session, run: IngestionRun) -> None:
    _finish_ingestion_run(session, run, RUN_STATUS_FAILED)


def _finish_ingestion_run(
    session: Session,
    run: IngestionRun,
    final_status: str,
) -> None:
    if run.status != RUN_STATUS_RUNNING:
        raise ValueError(
            f"ingestion run can only finish from '{RUN_STATUS_RUNNING}', "
            f"current status is '{run.status}'"
        )

    run.status = final_status
    run.finished_at = datetime.now(UTC)
    session.flush()


def register_ingestion_item(
    session: Session,
    run: IngestionRun,
    *,
    source_record_id: str,
    source_url: str | None = None,
    raw_payload: dict | None = None,
) -> IngestionItem:
    if run.id is None:
        raise ValueError("ingestion run must be persisted before registering items")

    if run.status != RUN_STATUS_RUNNING:
        raise ValueError("items can only be registered in a running ingestion run")

    item = IngestionItem(
        run_id=run.id,
        source_record_id=source_record_id,
        source_url=source_url,
        status=ITEM_STATUS_DISCOVERED,
        raw_payload=raw_payload,
    )
    session.add(item)
    session.flush()
    return item


def transition_ingestion_item(
    session: Session,
    item: IngestionItem,
    *,
    to_status: str,
    error_code: str | None = None,
    error_message: str | None = None,
    error_class: str | None = None,
) -> None:
    allowed = _ITEM_TRANSITIONS.get(item.status)
    if allowed is None:
        raise ValueError(f"unknown ingestion item status '{item.status}'")

    if to_status not in allowed:
        raise ValueError(
            f"invalid ingestion item transition '{item.status}' -> '{to_status}'"
        )

    if to_status == ITEM_STATUS_FAILED:
        if not error_code:
            raise ValueError("error_code is required when marking an item as failed")
        item.error_code = error_code
        item.error_message = error_message
        item.error_class = error_class
    elif (
        error_code is not None
        or error_message is not None
        or error_class is not None
    ):
        raise ValueError("error details are only allowed for failed items")

    item.status = to_status
    session.flush()



def retry_failed_ingestion_item(
    session: Session,
    item: IngestionItem,
) -> None:
    if item.status != ITEM_STATUS_FAILED:
        raise ValueError("only failed ingestion items can be retried")

    item.status = ITEM_STATUS_FETCHING
    item.retry_count = (item.retry_count or 0) + 1
    item.error_code = None
    item.error_message = None
    item.error_class = None
    session.flush()
