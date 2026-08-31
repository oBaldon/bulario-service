from dataclasses import dataclass

from sqlalchemy.orm import Session

from bulario_service.anvisa import AnvisaBularioConnector
from bulario_service.anvisa_documents import AnvisaDocumentDownloader
from bulario_service.document_storage import LocalDocumentStorage
from bulario_service.document_text import PdfTextExtractor
from bulario_service.e2e_pipeline import (
    E2EPipelineError,
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
    discovered_count: int
    processed_count: int
    ready_count: int
    failed_count: int
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
    publish: PublishFunction = publish_candidate,
) -> BatchIngestionResult:
    """
    Process the first discovery page as one multi-product ingestion run.

    Pagination beyond the first page, checkpoint and resume belong to the
    following Sprint 02 steps. This coordinator intentionally establishes
    only the multi-product run boundary and per-product failure isolation.
    """
    if page_size < 1:
        raise ValueError("page_size must be greater than zero")

    run = start_ingestion_run(session)
    session.commit()
    run_id = _require_id(run.id, "run")

    try:
        discovery = connector.discover_page(
            page=1,
            page_size=page_size,
            period_start=period_start,
            period_end=period_end,
        )
    except Exception as exc:
        session.rollback()
        _finish_run_as_failed(session, run_id)
        raise BatchIngestionError(
            "batch discovery failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    results: list[BatchItemResult] = []

    for product in discovery.items:
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
            results.append(
                BatchItemResult(
                    source_product_id=processed.source_product_id,
                    status="ready",
                    item_id=processed.item_id,
                    source_document_id=processed.source_document_id,
                    publish_action=processed.publish_action,
                    public_row_id=processed.public_row_id,
                )
            )
        except Exception as exc:
            # process_discovered_product is responsible for rolling back the
            # product transaction and persisting the item failure whenever an
            # item was created. The coordinator deliberately continues.
            results.append(
                BatchItemResult(
                    source_product_id=product.source_product_id,
                    status="failed",
                    error_code=type(exc).__name__[:64],
                    error_message=str(exc)[:2000],
                )
            )

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
        discovered_count=len(discovery.items),
        processed_count=len(results),
        ready_count=ready_count,
        failed_count=failed_count,
        items=tuple(results),
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
