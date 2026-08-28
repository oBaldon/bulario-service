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
    start_ingestion_run,
    transition_ingestion_item,
)
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

    item_id: int | None = None

    try:
        discovery = connector.discover_page(
            page=1,
            page_size=1,
            period_start=period_start,
            period_end=period_end,
        )
        if not discovery.items:
            raise E2EPipelineError("ANVISA discovery returned no products")

        product = discovery.items[0]
        _validate_discovered_product(product)

        item = register_ingestion_item(
            session,
            run,
            source_record_id=(
                f"anvisa-product:{product.source_product_id}"
            ),
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

        detail = connector.get_product_detail(product.source_product_id)
        current = _current_version(detail.versions)

        downloaded = []
        for kind, token in (
            ("patient", current.patient_token),
            ("professional", current.professional_token),
        ):
            if not token:
                raise E2EPipelineError(
                    "current version is missing required document token "
                    f"kind={kind} "
                    f"source_document_id={current.source_document_id}"
                )
            downloaded.append(
                downloader.download(
                    source_document_id=current.source_document_id,
                    kind=kind,
                    token=token,
                )
            )

        stored = tuple(
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
        }
        item.source_fingerprint = fingerprint

        persist_operational_version(
            session,
            product=product,
            version=current,
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
        complete_ingestion_run(session, run)
        session.commit()

        return E2EPipelineResult(
            run_id=run_id,
            item_id=item_id,
            source_product_id=product.source_product_id,
            source_document_id=current.source_document_id,
            publish_action=publication.action,
            public_row_id=publication.row_id,
        )

    except Exception as exc:
        session.rollback()
        _mark_failed(
            session,
            run_id=run_id,
            item_id=item_id,
            error=exc,
        )
        if isinstance(exc, E2EPipelineError):
            raise
        raise E2EPipelineError(
            f"controlled E2E pipeline failed: {type(exc).__name__}: {exc}"
        ) from exc


def _mark_failed(
    session: Session,
    *,
    run_id: int,
    item_id: int | None,
    error: Exception,
) -> None:
    from bulario_service.models import IngestionItem, IngestionRun

    persisted_run = session.get(IngestionRun, run_id)
    if persisted_run is None:
        raise E2EPipelineError(
            f"cannot recover ingestion run after failure run_id={run_id}"
        )

    if item_id is not None:
        persisted_item = session.get(IngestionItem, item_id)
        if persisted_item is not None and persisted_item.status not in {
            ITEM_STATUS_READY,
            ITEM_STATUS_FAILED,
        }:
            transition_ingestion_item(
                session,
                persisted_item,
                to_status=ITEM_STATUS_FAILED,
                error_code=type(error).__name__[:64],
                error_message=str(error)[:2000],
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
