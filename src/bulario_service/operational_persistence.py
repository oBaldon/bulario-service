import hashlib
import json
from dataclasses import dataclass
from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from bulario_service.anvisa import BulaVersion, DiscoveredProduct
from bulario_service.document_storage import StoredBulaDocument
from bulario_service.models import (
    BularioDocumentArtifact,
    BularioDocumentVersion,
    BularioProduct,
    IngestionItem,
)


class OperationalPersistenceError(RuntimeError):
    """Base error for operational persistence."""


class OperationalPersistenceConflictError(OperationalPersistenceError):
    """Raised when an immutable source identity changes materially."""


@dataclass(frozen=True)
class PersistedOperationalVersion:
    product: BularioProduct
    version: BularioDocumentVersion
    artifacts: tuple[BularioDocumentArtifact, ...]


def compute_source_fingerprint(
    *,
    product: DiscoveredProduct,
    version: BulaVersion,
) -> str:
    stable_payload = {
        "source_product_id": product.source_product_id,
        "source_document_id": version.source_document_id,
        "registration_number": version.registration_number,
        "expedient": version.expedient,
        "publication_date": version.publication_date,
        "status": version.status,
    }
    canonical = json.dumps(
        stable_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def persist_operational_version(
    session: Session,
    *,
    product: DiscoveredProduct,
    version: BulaVersion,
    stored_documents: Sequence[StoredBulaDocument],
    ingestion_item: IngestionItem | None = None,
) -> PersistedOperationalVersion:
    _validate_inputs(
        product=product,
        version=version,
        stored_documents=stored_documents,
        ingestion_item=ingestion_item,
    )

    operational_product = session.scalar(
        select(BularioProduct).where(
            BularioProduct.source_product_id == product.source_product_id
        )
    )
    if operational_product is None:
        operational_product = BularioProduct(
            source_product_id=product.source_product_id,
            registration_number=product.registration_number,
            product_name=product.product_name,
            company_name=product.company_name,
            company_cnpj=product.company_cnpj,
            process_number=product.process_number,
        )
        session.add(operational_product)
        session.flush()
    else:
        _refresh_product_metadata(operational_product, product)

    fingerprint = compute_source_fingerprint(
        product=product,
        version=version,
    )

    operational_version = session.scalar(
        select(BularioDocumentVersion).where(
            BularioDocumentVersion.source_document_id
            == version.source_document_id
        )
    )

    if operational_version is None:
        operational_version = BularioDocumentVersion(
            product_id=operational_product.id,
            last_ingestion_item_id=(
                ingestion_item.id if ingestion_item is not None else None
            ),
            source_document_id=version.source_document_id,
            expedient=version.expedient,
            registration_number=version.registration_number,
            source_publication_date=version.publication_date,
            status=version.status,
            is_current=version.current,
            source_fingerprint=fingerprint,
        )
        session.add(operational_version)
        session.flush()
    else:
        if operational_version.product_id != operational_product.id:
            raise OperationalPersistenceConflictError(
                "source_document_id is already linked to another product "
                f"source_document_id={version.source_document_id}"
            )
        if operational_version.source_fingerprint != fingerprint:
            raise OperationalPersistenceConflictError(
                "source fingerprint changed for existing document version "
                f"source_document_id={version.source_document_id}"
            )

        operational_version.is_current = version.current
        if ingestion_item is not None:
            operational_version.last_ingestion_item_id = ingestion_item.id

    if version.current:
        session.execute(
            update(BularioDocumentVersion)
            .where(
                BularioDocumentVersion.product_id == operational_product.id,
                BularioDocumentVersion.id != operational_version.id,
                BularioDocumentVersion.is_current.is_(True),
            )
            .values(is_current=False)
        )

    persisted_artifacts: list[BularioDocumentArtifact] = []
    for stored in stored_documents:
        artifact = session.scalar(
            select(BularioDocumentArtifact).where(
                BularioDocumentArtifact.document_version_id
                == operational_version.id,
                BularioDocumentArtifact.kind == stored.kind,
            )
        )

        if artifact is None:
            artifact = BularioDocumentArtifact(
                document_version_id=operational_version.id,
                kind=stored.kind,
                storage_key=stored.storage_key,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                content_type=None,
            )
            session.add(artifact)
            session.flush()
        else:
            _assert_artifact_unchanged(
                artifact=artifact,
                stored=stored,
            )

        persisted_artifacts.append(artifact)

    session.flush()

    return PersistedOperationalVersion(
        product=operational_product,
        version=operational_version,
        artifacts=tuple(persisted_artifacts),
    )


def _refresh_product_metadata(
    target: BularioProduct,
    source: DiscoveredProduct,
) -> None:
    target.registration_number = source.registration_number
    target.product_name = source.product_name
    target.company_name = source.company_name
    target.company_cnpj = source.company_cnpj
    target.process_number = source.process_number


def _validate_inputs(
    *,
    product: DiscoveredProduct,
    version: BulaVersion,
    stored_documents: Sequence[StoredBulaDocument],
    ingestion_item: IngestionItem | None,
) -> None:
    if ingestion_item is not None and ingestion_item.id is None:
        raise ValueError("ingestion_item must be persisted before use")

    kinds: set[str] = set()
    for stored in stored_documents:
        if stored.source_product_id != product.source_product_id:
            raise ValueError(
                "stored document source_product_id does not match product"
            )
        if stored.source_document_id != version.source_document_id:
            raise ValueError(
                "stored document source_document_id does not match version"
            )
        if stored.kind in kinds:
            raise ValueError(
                f"duplicate stored document kind '{stored.kind}'"
            )
        kinds.add(stored.kind)


def _assert_artifact_unchanged(
    *,
    artifact: BularioDocumentArtifact,
    stored: StoredBulaDocument,
) -> None:
    if (
        artifact.sha256 != stored.sha256
        or artifact.storage_key != stored.storage_key
        or artifact.size_bytes != stored.size_bytes
    ):
        raise OperationalPersistenceConflictError(
            "stored artifact changed for existing logical document "
            f"source_document_id={stored.source_document_id} "
            f"kind={stored.kind}"
        )
