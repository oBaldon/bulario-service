import argparse
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from bulario_service.config import load_settings
from bulario_service.db import create_database_engine
from bulario_service.document_storage import (
    LocalDocumentStorage,
    StoredBulaDocument,
)
from bulario_service.document_text import (
    DocumentTextExtractionError,
    PdfTextExtractor,
)
from bulario_service.models import (
    BularioDocumentArtifact,
    BularioDocumentVersion,
    BularioProduct,
)
from bulario_service.operational_persistence import (
    OperationalPersistenceError,
)
from bulario_service.operational_text_persistence import (
    persist_text_artifact,
)


DEFAULT_STORAGE_ROOT = Path("storage")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extrai, normaliza e persiste artefatos textuais da versão "
            "operacional vigente mais recente."
        )
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=DEFAULT_STORAGE_ROOT,
    )
    return parser


def run_smoke(*, storage_root: Path) -> int:
    engine = None
    try:
        settings = load_settings()
        engine = create_database_engine(settings)
        storage = LocalDocumentStorage(storage_root)
        extractor = PdfTextExtractor()

        with Session(engine) as session:
            version = session.scalar(
                select(BularioDocumentVersion)
                .where(BularioDocumentVersion.is_current.is_(True))
                .order_by(BularioDocumentVersion.id.desc())
            )
            if version is None:
                print("Nenhuma versão operacional vigente encontrada.")
                return 0

            product = session.get(BularioProduct, version.product_id)
            if product is None:
                raise RuntimeError("operational product not found")

            artifacts = tuple(
                session.scalars(
                    select(BularioDocumentArtifact)
                    .where(
                        BularioDocumentArtifact.document_version_id
                        == version.id
                    )
                    .order_by(BularioDocumentArtifact.kind)
                )
            )

            persisted_count = 0
            for artifact in artifacts:
                stored = StoredBulaDocument(
                    source_product_id=product.source_product_id,
                    source_document_id=version.source_document_id,
                    kind=artifact.kind,
                    storage_key=artifact.storage_key,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                )
                extracted = extractor.extract(
                    pdf_path=storage.resolve(stored.storage_key),
                    stored_document=stored,
                )
                persisted = persist_text_artifact(
                    session,
                    extracted=extracted,
                )
                print(
                    "Text persisted "
                    f"kind={artifact.kind} "
                    f"source_document_id={version.source_document_id} "
                    f"text_artifact_id={persisted.text_artifact.id} "
                    f"document_sha256={artifact.sha256} "
                    f"text_sha256={persisted.text_artifact.text_sha256} "
                    f"characters={persisted.text_artifact.character_count} "
                    f"normalization_version="
                    f"{persisted.text_artifact.normalization_version}"
                )
                persisted_count += 1

            session.commit()

        print(f"persisted_texts={persisted_count}")
        return 0

    except (
        DocumentTextExtractionError,
        OperationalPersistenceError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(
            f"ANVISA text persistence smoke failed: {exc}",
            file=sys.stderr,
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(run_smoke(storage_root=args.storage_root))


if __name__ == "__main__":
    main()
