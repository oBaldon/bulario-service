from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from bulario_service.anvisa import (
    AnvisaBularioConnector,
    BulaVersion,
    DiscoveredProduct,
)
from bulario_service.anvisa_documents import AnvisaDocumentDownloader
from bulario_service.document_storage import LocalDocumentStorage
from bulario_service.document_text import PdfTextExtractor
from bulario_service.ingestion import (
    ITEM_STATUS_DOWNLOADED,
    ITEM_STATUS_FAILED,
    ITEM_STATUS_FETCHING,
    ITEM_STATUS_NORMALIZED,
    ITEM_STATUS_READY,
    complete_ingestion_run,
    fail_ingestion_run,
    register_ingestion_item,
    retry_failed_ingestion_item,
    start_ingestion_run,
    transition_ingestion_item,
)
from bulario_service.models import IngestionItem, IngestionRun
from bulario_service.operational_persistence import (
    compute_source_fingerprint,
    persist_operational_version,
)
from bulario_service.operational_text_persistence import persist_text_artifact
from bulario_service.publication_contract import (
    SOURCE_URL,
    build_publication_candidate,
)
from bulario_service.publication_publisher import (
    PublishResult,
    publish_candidate,
)
from bulario_service.retry_policy import classify_exception


class E2EPipelineError(RuntimeError):
    """Raised when the controlled end-to-end pipeline cannot complete."""


@dataclass(frozen=True)
class E2EPipelineResult:
    run_id: int
    item_id: int
    source_product_id: int
    source_document_id: int
    publish_action: str
    public_row_id: int


@dataclass(frozen=True)
class ProcessedProductResult:
    item_id: int
    source_product_id: int
    source_document_id: int
    publish_action: str
    public_row_id: int


PublishFunction = Callable[..., PublishResult]


def run_single_product_pipeline(
    session: Session,
    *,
    connector: AnvisaBularioConnector,
    downloader: AnvisaDocumentDownloader,
    storage: LocalDocumentStorage,
    extractor: PdfTextExtractor,
    period_start: str,
    period_end: str,
    publish: PublishFunction = publish_candidate,
) -> E2EPipelineResult:
    run = start_ingestion_run(session)
    session.commit()
    run_id = _require_id(run.id, "run")

    try:
        discovery = connector.discover_page(
            page=1,
            page_size=1,
            period_start=period_start,
            period_end=period_end,
        )
        if not discovery.items:
            raise E2EPipelineError("ANVISA discovery returned no products")

        result = process_discovered_product(
            session,
            run=run,
            product=discovery.items[0],
            connector=connector,
            downloader=downloader,
            storage=storage,
            extractor=extractor,
            publish=publish,
        )

        persisted_run = session.get(IngestionRun, run_id)
        if persisted_run is None:
            raise E2EPipelineError(
                f"cannot reload ingestion run run_id={run_id}"
            )
        complete_ingestion_run(session, persisted_run)
        session.commit()

        return E2EPipelineResult(
            run_id=run_id,
            item_id=result.item_id,
            source_product_id=result.source_product_id,
            source_document_id=result.source_document_id,
            publish_action=result.publish_action,
            public_row_id=result.public_row_id,
        )

    except Exception as exc:
        if not isinstance(exc, E2EPipelineError):
            session.rollback()
        _mark_run_failed(
            session,
            run_id=run_id,
            error=exc,
        )
        if isinstance(exc, E2EPipelineError):
            raise
        raise E2EPipelineError(
            f"controlled E2E pipeline failed: {type(exc).__name__}: {exc}"
        ) from exc


def process_discovered_product(
    session: Session,
    *,
    run: IngestionRun,
    product: DiscoveredProduct,
    connector: AnvisaBularioConnector,
    downloader: AnvisaDocumentDownloader,
    storage: LocalDocumentStorage,
    extractor: PdfTextExtractor,
    retry_item: IngestionItem | None = None,
    publish: PublishFunction = publish_candidate,
) -> ProcessedProductResult:
    """Process one discovered product inside an existing ingestion run."""
    if run.status != "running":
        raise E2EPipelineError(
            "product can only be processed inside a running ingestion run"
        )

    _validate_discovered_product(product)

    source_record_id = f"anvisa-product:{product.source_product_id}"
    if retry_item is None:
        item = register_ingestion_item(
            session,
            run,
            source_record_id=source_record_id,
            source_url=SOURCE_URL,
            raw_payload=product.raw_payload,
        )
        session.commit()
        item_id = _require_id(item.id, "item")

        transition_ingestion_item(
            session,
            item,
            to_status=ITEM_STATUS_FETCHING,
        )
        session.commit()
    else:
        item = retry_item
        if item.run_id != run.id:
            raise E2EPipelineError(
                "retry item belongs to a different ingestion run"
            )
        if item.source_record_id != source_record_id:
            raise E2EPipelineError(
                "retry item source_record_id does not match product"
            )
        item_id = _require_id(item.id, "item")
        retry_failed_ingestion_item(session, item)
        session.commit()

    try:

        detail = connector.get_product_detail(product.source_product_id)
        current = _current_version(detail.versions)
        versions = _versions_for_persistence(
            detail.versions,
            current=current,
        )

        stored_by_document_id = {}
        for version in versions:
            downloaded = []
            for kind, token in (
                ("patient", version.patient_token),
                ("professional", version.professional_token),
            ):
                if not token:
                    if version.current:
                        raise E2EPipelineError(
                            "current version is missing required document token "
                            f"kind={kind} "
                            f"source_document_id={version.source_document_id}"
                        )
                    continue

                downloaded.append(
                    downloader.download(
                        source_document_id=version.source_document_id,
                        kind=kind,
                        token=token,
                    )
                )

            stored_by_document_id[version.source_document_id] = tuple(
                storage.store(
                    source_product_id=product.source_product_id,
                    document=document,
                )
                for document in downloaded
            )

        transition_ingestion_item(
            session,
            item,
            to_status=ITEM_STATUS_DOWNLOADED,
        )
        session.commit()

        fingerprint = compute_source_fingerprint(
            product=product,
            version=current,
        )
        item.normalized_payload = {
            "source_product_id": product.source_product_id,
            "source_document_id": current.source_document_id,
            "registration_number": current.registration_number,
            "expedient": current.expedient,
            "publication_date": current.publication_date,
            "status": current.status,
            "version_count": len(versions),
        }
        item.source_fingerprint = fingerprint

        for version in versions:
            stored = stored_by_document_id[version.source_document_id]
            persist_operational_version(
                session,
                product=product,
                version=version,
                stored_documents=stored,
                ingestion_item=item,
            )

            for stored_document in stored:
                extracted = extractor.extract(
                    pdf_path=storage.resolve(stored_document.storage_key),
                    stored_document=stored_document,
                )
                persist_text_artifact(
                    session,
                    extracted=extracted,
                )

        transition_ingestion_item(
            session,
            item,
            to_status=ITEM_STATUS_NORMALIZED,
        )
        session.commit()

        candidate = build_publication_candidate(
            session,
            source_document_id=current.source_document_id,
        )
        publication = publish(
            session,
            candidate=candidate,
        )

        transition_ingestion_item(
            session,
            item,
            to_status=ITEM_STATUS_READY,
        )

        return ProcessedProductResult(
            item_id=item_id,
            source_product_id=product.source_product_id,
            source_document_id=current.source_document_id,
            publish_action=publication.action,
            public_row_id=publication.row_id,
        )

    except Exception as exc:
        session.rollback()
        _mark_item_failed(
            session,
            item_id=item_id,
            error=exc,
        )
        if isinstance(exc, E2EPipelineError):
            raise
        raise E2EPipelineError(
            "product pipeline failed "
            f"source_product_id={product.source_product_id}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

def _mark_item_failed(
    session: Session,
    *,
    item_id: int,
    error: Exception,
) -> None:
    from bulario_service.models import IngestionItem

    persisted_item = session.get(IngestionItem, item_id)
    if persisted_item is None:
        raise E2EPipelineError(
            f"cannot recover ingestion item after failure item_id={item_id}"
        )

    if persisted_item.status not in {
        ITEM_STATUS_READY,
        ITEM_STATUS_FAILED,
    }:
        classification = classify_exception(error)
        root_error = error
        while root_error.__cause__ is not None:
            root_error = root_error.__cause__
        transition_ingestion_item(
            session,
            persisted_item,
            to_status=ITEM_STATUS_FAILED,
            error_code=type(root_error).__name__[:64],
            error_message=str(root_error)[:2000],
            error_class=classification.error_class,
        )

    session.commit()


def _mark_run_failed(
    session: Session,
    *,
    run_id: int,
    error: Exception,
) -> None:
    persisted_run = session.get(IngestionRun, run_id)
    if persisted_run is None:
        raise E2EPipelineError(
            f"cannot recover ingestion run after failure run_id={run_id}"
        )

    if persisted_run.status == "running":
        fail_ingestion_run(session, persisted_run)

    session.commit()



def _current_version(
    versions: tuple[BulaVersion, ...],
) -> BulaVersion:
    current = tuple(version for version in versions if version.current)
    if len(current) != 1:
        raise E2EPipelineError(
            "product detail must contain exactly one current version"
        )
    return current[0]


def _versions_for_persistence(
    versions: tuple[BulaVersion, ...],
    *,
    current: BulaVersion,
) -> tuple[BulaVersion, ...]:
    if not versions:
        raise E2EPipelineError(
            "product detail must contain at least one document version"
        )

    seen: set[int] = set()
    historical: list[BulaVersion] = []
    for version in versions:
        if version.source_document_id in seen:
            raise E2EPipelineError(
                "product detail contains duplicate source_document_id "
                f"source_document_id={version.source_document_id}"
            )
        seen.add(version.source_document_id)

        if version.source_document_id != current.source_document_id:
            historical.append(version)

    return (*historical, current)


def _validate_discovered_product(product: DiscoveredProduct) -> None:
    if not product.product_name:
        raise E2EPipelineError(
            "discovered product is missing product_name "
            f"source_product_id={product.source_product_id}"
        )


def _require_id(value: int | None, entity: str) -> int:
    if value is None:
        raise E2EPipelineError(f"{entity} id was not persisted")
    return value
