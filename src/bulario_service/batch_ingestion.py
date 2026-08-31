from dataclasses import dataclass
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from bulario_service.anvisa import AnvisaBularioConnector
from bulario_service.anvisa_documents import AnvisaDocumentDownloader
from bulario_service.document_storage import LocalDocumentStorage
from bulario_service.document_text import PdfTextExtractor
from bulario_service.e2e_pipeline import (
    PublishFunction,
    process_discovered_product,
)
from bulario_service.ingestion import (
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    checkpoint_ingestion_run,
    complete_ingestion_run,
    fail_ingestion_run,
    pause_ingestion_run,
    resume_ingestion_run,
    start_ingestion_run,
)
from bulario_service.models import IngestionItem, IngestionRun
from bulario_service.publication_publisher import publish_candidate


BATCH_MODE = "batch"
FULL_MODE = "full"
_ALLOWED_MODES = {BATCH_MODE, FULL_MODE}
PRODUCT_SOURCE_PREFIX = "anvisa-product:"


class BatchIngestionError(RuntimeError):
    """Raised when a batch run cannot be created, resumed or finalized safely."""


@dataclass(frozen=True)
class BatchItemResult:
    source_product_id: int
    status: str
    item_id: int | None = None
    source_document_id: int | None = None
    publish_action: str | None = None
    public_row_id: int | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class BatchIngestionResult:
    run_id: int
    run_status: str
    resumed: bool
    start_page: int
    last_completed_page: int
    pages_fetched: int
    discovered_count: int
    duplicate_count: int
    skipped_terminal_count: int
    processed_count: int
    ready_count: int
    failed_count: int
    stopped_by_page_limit: bool
    stopped_by_product_limit: bool
    source_total_elements: int | None
    invocation_duration_seconds: float
    items: tuple[BatchItemResult, ...]


def run_batch_ingestion(
    session: Session,
    *,
    connector: AnvisaBularioConnector,
    downloader: AnvisaDocumentDownloader,
    storage: LocalDocumentStorage,
    extractor: PdfTextExtractor,
    period_start: str | None,
    period_end: str | None,
    page_size: int | None = None,
    max_pages: int | None = 1,
    max_products: int | None = None,
    resume_run_id: int | None = None,
    run_mode: str = BATCH_MODE,
    publish: PublishFunction = publish_candidate,
) -> BatchIngestionResult:
    """
    Process discovery pages as one resumable multi-product ingestion run.

    Step 25 persists the run window and page checkpoint. A controlled stop by
    max_pages/max_products pauses the run. Resume reuses the original window,
    restarts from the first page not fully checkpointed and skips terminal
    ingestion items already persisted in the same run.
    """
    _validate_limits(
        page_size=page_size,
        max_pages=max_pages,
        max_products=max_products,
    )
    _validate_run_mode(run_mode)
    invocation_started = time.monotonic()

    if resume_run_id is None:
        resolved_page_size = page_size or 10
        if not period_start or not period_end:
            raise ValueError(
                "period_start and period_end are required for a new batch run"
            )
        run = start_ingestion_run(
            session,
            mode=run_mode,
            period_start=period_start,
            period_end=period_end,
            page_size=resolved_page_size,
        )
        session.commit()
        run_id = _require_id(run.id, "run")
        resolved_period_start = period_start
        resolved_period_end = period_end
        start_page = 1
        resumed = False
    else:
        run = _load_resumable_run(
            session,
            resume_run_id,
            expected_mode=run_mode,
        )
        (
            resolved_period_start,
            resolved_period_end,
            resolved_page_size,
        ) = _validate_resume_compatibility(
            run,
            period_start=period_start,
            period_end=period_end,
            page_size=page_size,
        )
        start_page = run.last_completed_page + 1
        resume_ingestion_run(session, run)
        session.commit()
        run_id = _require_id(run.id, "run")
        resumed = True

    terminal_product_ids = _load_terminal_product_ids(
        session,
        run_id=run_id,
    )
    seen_this_invocation: set[int] = set()
    results: list[BatchItemResult] = []
    duplicate_count = 0
    skipped_terminal_count = 0
    pages_fetched = 0
    stopped_by_page_limit = False
    stopped_by_product_limit = False
    page = start_page
    last_discovery_was_last = False
    source_total_elements: int | None = None

    try:
        while True:
            if max_pages is not None and pages_fetched >= max_pages:
                stopped_by_page_limit = True
                break

            discovery = connector.discover_page(
                page=page,
                page_size=resolved_page_size,
                period_start=resolved_period_start,
                period_end=resolved_period_end,
            )
            pages_fetched += 1
            source_total_elements = discovery.total_elements
            last_discovery_was_last = discovery.last
            page_fully_processed = True

            for product in discovery.items:
                product_id = product.source_product_id

                if product_id in seen_this_invocation:
                    duplicate_count += 1
                    continue

                seen_this_invocation.add(product_id)

                if product_id in terminal_product_ids:
                    skipped_terminal_count += 1
                    continue

                if (
                    max_products is not None
                    and len(results) >= max_products
                ):
                    stopped_by_product_limit = True
                    page_fully_processed = False
                    break

                result = _process_product(
                    session,
                    run_id=run_id,
                    product=product,
                    connector=connector,
                    downloader=downloader,
                    storage=storage,
                    extractor=extractor,
                    publish=publish,
                )
                results.append(result)
                terminal_product_ids.add(product_id)

            if page_fully_processed:
                _checkpoint_page(
                    session,
                    run_id=run_id,
                    completed_page=page,
                )

            if (
                not stopped_by_product_limit
                and max_products is not None
                and len(results) >= max_products
                and not discovery.last
            ):
                stopped_by_product_limit = True

            if stopped_by_product_limit:
                break

            if discovery.last:
                break

            if discovery.total_pages < 1:
                raise BatchIngestionError(
                    "discovery pagination reported invalid total_pages="
                    f"{discovery.total_pages}"
                )

            if page >= discovery.total_pages:
                raise BatchIngestionError(
                    "discovery pagination is inconsistent: "
                    "last=false on final page"
                )

            page += 1

    except Exception as exc:
        session.rollback()
        _finish_run_as_failed(session, run_id)
        if isinstance(exc, BatchIngestionError):
            raise
        raise BatchIngestionError(
            "batch discovery failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    persisted_run = _get_running_run(session, run_id)
    should_pause = (
        stopped_by_product_limit
        or (stopped_by_page_limit and not last_discovery_was_last)
    )

    if should_pause:
        pause_ingestion_run(session, persisted_run)
    else:
        all_failed_count = _count_items_with_status(
            session,
            run_id=run_id,
            status="failed",
        )
        if all_failed_count:
            fail_ingestion_run(session, persisted_run)
        else:
            complete_ingestion_run(session, persisted_run)
    session.commit()

    ready_count = sum(item.status == "ready" for item in results)
    failed_count = sum(item.status == "failed" for item in results)

    return BatchIngestionResult(
        run_id=run_id,
        run_status=persisted_run.status,
        resumed=resumed,
        start_page=start_page,
        last_completed_page=persisted_run.last_completed_page,
        pages_fetched=pages_fetched,
        discovered_count=len(seen_this_invocation),
        duplicate_count=duplicate_count,
        skipped_terminal_count=skipped_terminal_count,
        processed_count=len(results),
        ready_count=ready_count,
        failed_count=failed_count,
        stopped_by_page_limit=stopped_by_page_limit,
        stopped_by_product_limit=stopped_by_product_limit,
        source_total_elements=source_total_elements,
        invocation_duration_seconds=round(
            time.monotonic() - invocation_started,
            3,
        ),
        items=tuple(results),
    )


def _process_product(
    session: Session,
    *,
    run_id: int,
    product,
    connector: AnvisaBularioConnector,
    downloader: AnvisaDocumentDownloader,
    storage: LocalDocumentStorage,
    extractor: PdfTextExtractor,
    publish: PublishFunction,
) -> BatchItemResult:
    try:
        processed = process_discovered_product(
            session,
            run=_get_running_run(session, run_id),
            product=product,
            connector=connector,
            downloader=downloader,
            storage=storage,
            extractor=extractor,
            publish=publish,
        )
        session.commit()
        return BatchItemResult(
            source_product_id=processed.source_product_id,
            status="ready",
            item_id=processed.item_id,
            source_document_id=processed.source_document_id,
            publish_action=processed.publish_action,
            public_row_id=processed.public_row_id,
        )
    except Exception as exc:
        return BatchItemResult(
            source_product_id=product.source_product_id,
            status="failed",
            error_code=type(exc).__name__[:64],
            error_message=str(exc)[:2000],
        )


def _validate_run_mode(run_mode: str) -> None:
    if run_mode not in _ALLOWED_MODES:
        raise ValueError(
            "run_mode must be one of: "
            + ", ".join(sorted(_ALLOWED_MODES))
        )


def _validate_limits(
    *,
    page_size: int | None,
    max_pages: int | None,
    max_products: int | None,
) -> None:
    if page_size is not None and page_size < 1:
        raise ValueError("page_size must be greater than zero")
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be greater than zero when provided")
    if max_products is not None and max_products < 1:
        raise ValueError(
            "max_products must be greater than zero when provided"
        )


def _load_resumable_run(
    session: Session,
    run_id: int,
    *,
    expected_mode: str,
) -> IngestionRun:
    run = session.get(IngestionRun, run_id)
    if run is None:
        raise BatchIngestionError(
            f"cannot resume unknown ingestion run run_id={run_id}"
        )
    if run.status != RUN_STATUS_PAUSED:
        raise BatchIngestionError(
            "ingestion run is not resumable "
            f"run_id={run_id} status={run.status}"
        )
    if run.mode != expected_mode:
        raise BatchIngestionError(
            "ingestion run mode is not compatible with requested resume "
            f"run_id={run_id} persisted_mode={run.mode} "
            f"requested_mode={expected_mode}"
        )
    return run


def _validate_resume_compatibility(
    run: IngestionRun,
    *,
    period_start: str | None,
    period_end: str | None,
    page_size: int | None,
) -> tuple[str, str, int]:
    if not run.period_start or not run.period_end or not run.page_size:
        raise BatchIngestionError(
            f"ingestion run lacks resume metadata run_id={run.id}"
        )

    if period_start is not None and period_start != run.period_start:
        raise BatchIngestionError(
            "resume period_start differs from persisted run window"
        )
    if period_end is not None and period_end != run.period_end:
        raise BatchIngestionError(
            "resume period_end differs from persisted run window"
        )
    if page_size is not None and page_size != run.page_size:
        raise BatchIngestionError(
            "resume page_size differs from persisted run page_size"
        )

    return run.period_start, run.period_end, run.page_size


def _load_terminal_product_ids(
    session: Session,
    *,
    run_id: int,
) -> set[int]:
    items = session.scalars(
        select(IngestionItem).where(
            IngestionItem.run_id == run_id,
            IngestionItem.status.in_(("ready", "failed")),
        )
    )
    product_ids: set[int] = set()
    for item in items:
        value = _product_id_from_source_record(item.source_record_id)
        if value is not None:
            product_ids.add(value)
    return product_ids


def _count_items_with_status(
    session: Session,
    *,
    run_id: int,
    status: str,
) -> int:
    items = session.scalars(
        select(IngestionItem).where(
            IngestionItem.run_id == run_id,
            IngestionItem.status == status,
        )
    )
    return sum(1 for _ in items)


def _product_id_from_source_record(source_record_id: str) -> int | None:
    if not source_record_id.startswith(PRODUCT_SOURCE_PREFIX):
        return None
    raw_id = source_record_id[len(PRODUCT_SOURCE_PREFIX):]
    try:
        return int(raw_id)
    except ValueError:
        return None


def _checkpoint_page(
    session: Session,
    *,
    run_id: int,
    completed_page: int,
) -> None:
    run = _get_running_run(session, run_id)
    checkpoint_ingestion_run(
        session,
        run,
        completed_page=completed_page,
    )
    session.commit()


def _get_running_run(session: Session, run_id: int) -> IngestionRun:
    run = session.get(IngestionRun, run_id)
    if run is None:
        raise BatchIngestionError(
            f"cannot reload ingestion run run_id={run_id}"
        )
    if run.status != RUN_STATUS_RUNNING:
        raise BatchIngestionError(
            f"ingestion run is not running run_id={run_id} status={run.status}"
        )
    return run


def _finish_run_as_failed(session: Session, run_id: int) -> None:
    run = session.get(IngestionRun, run_id)
    if run is None:
        raise BatchIngestionError(
            f"cannot recover ingestion run after discovery failure run_id={run_id}"
        )
    if run.status == RUN_STATUS_RUNNING:
        fail_ingestion_run(session, run)
    session.commit()


def _require_id(value: int | None, entity: str) -> int:
    if value is None:
        raise BatchIngestionError(f"{entity} id was not persisted")
    return value
