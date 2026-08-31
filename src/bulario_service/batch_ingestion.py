from dataclasses import dataclass

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
    complete_ingestion_run,
    fail_ingestion_run,
    start_ingestion_run,
)
from bulario_service.models import IngestionRun
from bulario_service.publication_publisher import publish_candidate


class BatchIngestionError(RuntimeError):
    """Raised when a batch run cannot be created or finalized safely."""


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
    pages_fetched: int
    discovered_count: int
    duplicate_count: int
    processed_count: int
    ready_count: int
    failed_count: int
    stopped_by_page_limit: bool
    stopped_by_product_limit: bool
    items: tuple[BatchItemResult, ...]


def run_batch_ingestion(
    session: Session,
    *,
    connector: AnvisaBularioConnector,
    downloader: AnvisaDocumentDownloader,
    storage: LocalDocumentStorage,
    extractor: PdfTextExtractor,
    period_start: str,
    period_end: str,
    page_size: int = 10,
    max_pages: int | None = 1,
    max_products: int | None = None,
    publish: PublishFunction = publish_candidate,
) -> BatchIngestionResult:
    """
    Process discovery pages as one multi-product ingestion run.

    Sprint 02 Step 24 adds multi-page discovery, per-run product deduplication
    and explicit operational limits. Checkpoint and resume remain outside this
    step and are introduced only in Step 25.
    """
    _validate_limits(
        page_size=page_size,
        max_pages=max_pages,
        max_products=max_products,
    )

    run = start_ingestion_run(session)
    session.commit()
    run_id = _require_id(run.id, "run")

    results: list[BatchItemResult] = []
    seen_product_ids: set[int] = set()
    duplicate_count = 0
    pages_fetched = 0
    stopped_by_page_limit = False
    stopped_by_product_limit = False
    page = 1

    try:
        while True:
            if max_pages is not None and pages_fetched >= max_pages:
                stopped_by_page_limit = True
                break

            discovery = connector.discover_page(
                page=page,
                page_size=page_size,
                period_start=period_start,
                period_end=period_end,
            )
            pages_fetched += 1

            for product in discovery.items:
                product_id = product.source_product_id
                if product_id in seen_product_ids:
                    duplicate_count += 1
                    continue

                if (
                    max_products is not None
                    and len(seen_product_ids) >= max_products
                ):
                    stopped_by_product_limit = True
                    break

                seen_product_ids.add(product_id)
                results.append(
                    _process_product(
                        session,
                        run_id=run_id,
                        product=product,
                        connector=connector,
                        downloader=downloader,
                        storage=storage,
                        extractor=extractor,
                        publish=publish,
                    )
                )

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

    ready_count = sum(item.status == "ready" for item in results)
    failed_count = sum(item.status == "failed" for item in results)

    persisted_run = session.get(IngestionRun, run_id)
    if persisted_run is None:
        raise BatchIngestionError(
            f"cannot reload ingestion run run_id={run_id}"
        )

    if failed_count:
        fail_ingestion_run(session, persisted_run)
    else:
        complete_ingestion_run(session, persisted_run)
    session.commit()

    return BatchIngestionResult(
        run_id=run_id,
        run_status=persisted_run.status,
        pages_fetched=pages_fetched,
        discovered_count=len(seen_product_ids),
        duplicate_count=duplicate_count,
        processed_count=len(results),
        ready_count=ready_count,
        failed_count=failed_count,
        stopped_by_page_limit=stopped_by_page_limit,
        stopped_by_product_limit=stopped_by_product_limit,
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
        # process_discovered_product owns the product transaction and persists
        # item failure whenever item creation already happened. The batch
        # coordinator isolates the failure and continues with other products.
        return BatchItemResult(
            source_product_id=product.source_product_id,
            status="failed",
            error_code=type(exc).__name__[:64],
            error_message=str(exc)[:2000],
        )


def _validate_limits(
    *,
    page_size: int,
    max_pages: int | None,
    max_products: int | None,
) -> None:
    if page_size < 1:
        raise ValueError("page_size must be greater than zero")
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be greater than zero when provided")
    if max_products is not None and max_products < 1:
        raise ValueError(
            "max_products must be greater than zero when provided"
        )


def _get_running_run(session: Session, run_id: int) -> IngestionRun:
    run = session.get(IngestionRun, run_id)
    if run is None:
        raise BatchIngestionError(
            f"cannot reload ingestion run run_id={run_id}"
        )
    if run.status != "running":
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
    if run.status == "running":
        fail_ingestion_run(session, run)
    session.commit()


def _require_id(value: int | None, entity: str) -> int:
    if value is None:
        raise BatchIngestionError(f"{entity} id was not persisted")
    return value
