from datetime import UTC, datetime

from sqlalchemy.orm import Session

from bulario_service.models import IngestionItem, IngestionRun


RUN_STATUS_RUNNING = "running"
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


def start_ingestion_run(session: Session) -> IngestionRun:
    run = IngestionRun(status=RUN_STATUS_RUNNING)
    session.add(run)
    session.flush()
    return run


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
    elif error_code is not None or error_message is not None:
        raise ValueError("error details are only allowed for failed items")

    item.status = to_status
    session.flush()
