from dataclasses import dataclass
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from bulario_service.anvisa import (
    AnvisaBularioConnector,
    DiscoveredProduct,
)
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
from bulario_service.retry_policy import (
    FailureClassification,
    classify_exception,
    classify_persisted_failure,
)


BATCH_MODE = "batch"
FULL_MODE = "full"
INCREMENTAL_MODE = "incremental"
_ALLOWED_MODES = {BATCH_MODE, FULL_MODE, INCREMENTAL_MODE}
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
    error_class: str | None = None
    retry_count: int = 0
    stop_run: bool = False


@dataclass(frozen=True)
class BatchIngestionResult:
    run_id: int
    run_status: str
    run_mode: str
    period_start: str
    period_end: str
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
    stopped_by_source_blocked: bool
    retry_count: int
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
    max_product_retries: int = 2,
    retry_backoff_seconds: float = 2.0,
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
    _validate_retry_policy(
        max_product_retries=max_product_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )
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

    terminal_product_ids: set[int] = set()
    seen_this_invocation: set[int] = set()
    results: list[BatchItemResult] = []
    duplicate_count = 0
    skipped_terminal_count = 0
    pages_fetched = 0
    stopped_by_page_limit = False
    stopped_by_product_limit = False
    stopped_by_source_blocked = False
    invocation_retry_count = 0
    page = start_page
    last_discovery_was_last = False
    source_total_elements: int | None = None

    try:
        if resumed:
            retry_items = _load_retryable_failed_items(
                session,
                run_id=run_id,
                max_product_retries=max_product_retries,
            )
            for retry_item in retry_items:
                if (
                    max_products is not None
                    and len(results) >= max_products
                ):
                    stopped_by_product_limit = True
                    break

                retry_product = _product_from_failed_item(retry_item)
                retry_result = _process_product(
                    session,
                    run_id=run_id,
                    product=retry_product,
                    connector=connector,
                    downloader=downloader,
                    storage=storage,
                    extractor=extractor,
                    publish=publish,
                    retry_item=retry_item,
                    max_product_retries=max_product_retries,
                    retry_backoff_seconds=retry_backoff_seconds,
                )
                results.append(retry_result)
                invocation_retry_count += retry_result.retry_count

                if retry_result.stop_run:
                    stopped_by_source_blocked = True
                    break

            if stopped_by_product_limit or stopped_by_source_blocked:
                terminal_product_ids = _load_terminal_product_ids(
                    session,
                    run_id=run_id,
                )
            else:
                terminal_product_ids = _load_terminal_product_ids(
                    session,
                    run_id=run_id,
                )
        else:
            terminal_product_ids = _load_terminal_product_ids(
                session,
                run_id=run_id,
            )

        while not (
            stopped_by_product_limit
            or stopped_by_source_blocked
        ):
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
                    retry_item=None,
                    max_product_retries=max_product_retries,
                    retry_backoff_seconds=retry_backoff_seconds,
                )
                results.append(result)
                invocation_retry_count += result.retry_count
                terminal_product_ids.add(product_id)

                if result.stop_run:
                    stopped_by_source_blocked = True
                    page_fully_processed = False
                    break

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

            if stopped_by_product_limit or stopped_by_source_blocked:
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
        or stopped_by_source_blocked
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

    if not persisted_run.period_start or not persisted_run.period_end:
        raise BatchIngestionError(
            f"ingestion run lacks persisted window run_id={run_id}"
        )

    return BatchIngestionResult(
        run_id=run_id,
        run_status=persisted_run.status,
        run_mode=persisted_run.mode or run_mode,
        period_start=persisted_run.period_start,
        period_end=persisted_run.period_end,
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
        stopped_by_source_blocked=stopped_by_source_blocked,
        retry_count=invocation_retry_count,
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
    product: DiscoveredProduct,
    connector: AnvisaBularioConnector,
    downloader: AnvisaDocumentDownloader,
    storage: LocalDocumentStorage,
    extractor: PdfTextExtractor,
    publish: PublishFunction,
    retry_item: IngestionItem | None,
    max_product_retries: int,
    retry_backoff_seconds: float,
) -> BatchItemResult:
    current_retry_item = retry_item
    retries_used = 0

    while True:
        if current_retry_item is not None:
            retries_used += 1

        try:
            processed = process_discovered_product(
                session,
                run=_get_running_run(session, run_id),
                product=product,
                connector=connector,
                downloader=downloader,
                storage=storage,
                extractor=extractor,
                retry_item=current_retry_item,
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
                retry_count=retries_used,
            )
        except Exception as exc:
            classification = classify_exception(exc)
            persisted_item = _get_product_item(
                session,
                run_id=run_id,
                source_product_id=product.source_product_id,
            )
            total_retry_count = (
                persisted_item.retry_count
                if persisted_item is not None
                else 0
            )

            if (
                classification.retryable
                and persisted_item is not None
                and total_retry_count < max_product_retries
            ):
                if retry_backoff_seconds:
                    time.sleep(retry_backoff_seconds)
                current_retry_item = persisted_item
                continue

            root_error = _root_error(exc)
            return BatchItemResult(
                source_product_id=product.source_product_id,
                status="failed",
                item_id=(
                    persisted_item.id
                    if persisted_item is not None
                    else None
                ),
                error_code=type(root_error).__name__[:64],
                error_message=str(root_error)[:2000],
                error_class=classification.error_class,
                retry_count=retries_used,
                stop_run=classification.stop_run,
            )



def _validate_run_mode(run_mode: str) -> None:
    if run_mode not in _ALLOWED_MODES:
        raise ValueError(
            "run_mode must be one of: "
            + ", ".join(sorted(_ALLOWED_MODES))
        )


def _validate_retry_policy(
    *,
    max_product_retries: int,
    retry_backoff_seconds: float,
) -> None:
    if max_product_retries < 0:
        raise ValueError("max_product_retries must be zero or greater")
    if retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds must be zero or greater")


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


def _load_retryable_failed_items(
    session: Session,
    *,
    run_id: int,
    max_product_retries: int,
) -> tuple[IngestionItem, ...]:
    items = session.scalars(
        select(IngestionItem)
        .where(
            IngestionItem.run_id == run_id,
            IngestionItem.status == "failed",
        )
        .order_by(IngestionItem.id)
    )
    retryable: list[IngestionItem] = []
    for item in items:
        classification = classify_persisted_failure(
            error_class=item.error_class,
            error_code=item.error_code,
            error_message=item.error_message,
        )
        if (
            classification.retryable
            and (item.retry_count or 0) < max_product_retries
        ):
            retryable.append(item)
    return tuple(retryable)


def _get_product_item(
    session: Session,
    *,
    run_id: int,
    source_product_id: int,
) -> IngestionItem | None:
    return session.scalar(
        select(IngestionItem).where(
            IngestionItem.run_id == run_id,
            IngestionItem.source_record_id
            == f"{PRODUCT_SOURCE_PREFIX}{source_product_id}",
        )
    )


def _product_from_failed_item(
    item: IngestionItem,
) -> DiscoveredProduct:
    source_product_id = _product_id_from_source_record(
        item.source_record_id
    )
    if source_product_id is None:
        raise BatchIngestionError(
            "cannot retry failed item with invalid source_record_id "
            f"item_id={item.id}"
        )

    payload = item.raw_payload
    if not isinstance(payload, dict):
        raise BatchIngestionError(
            f"cannot retry failed item without raw_payload item_id={item.id}"
        )

    return DiscoveredProduct(
        source_product_id=source_product_id,
        registration_number=_optional_payload_string(
            payload.get("numeroRegistro")
        ),
        product_name=_optional_payload_string(
            payload.get("nomeProduto")
        ),
        current_expedient=_optional_payload_string(
            payload.get("expediente")
        ),
        company_name=_optional_payload_string(
            payload.get("razaoSocial")
        ),
        company_cnpj=_optional_payload_string(payload.get("cnpj")),
        process_number=_optional_payload_string(
            payload.get("numProcesso")
        ),
        publication_date=_optional_payload_string(payload.get("data")),
        raw_payload=payload,
    )


def _optional_payload_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _root_error(error: Exception) -> Exception:
    current = error
    seen: set[int] = set()
    while (
        current.__cause__ is not None
        and id(current) not in seen
    ):
        seen.add(id(current))
        current = current.__cause__
    return current


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
