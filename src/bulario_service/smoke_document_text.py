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




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extrai e normaliza texto dos PDFs operacionais da versão "
            "documental vigente mais recente."
        )
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=None,
    )
    return parser


def run_smoke(*, storage_root: Path | None) -> int:
    engine = None
    try:
        settings = load_settings()
        engine = create_database_engine(settings)
        effective_storage_root = storage_root or settings.storage_root
        storage = LocalDocumentStorage(effective_storage_root)
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

        if not artifacts:
            print("Nenhum PDF operacional encontrado para a versão vigente.")
            return 0

        extracted_count = 0
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
            print(
                "Text extracted "
                f"kind={extracted.kind} "
                f"source_document_id={extracted.source_document_id} "
                f"document_sha256={extracted.document_sha256} "
                f"text_sha256={extracted.text_sha256} "
                f"characters={extracted.character_count} "
                f"storage_key={extracted.document_storage_key}"
            )
            extracted_count += 1

        print(f"extracted_texts={extracted_count}")
        return 0

    except (DocumentTextExtractionError, RuntimeError) as exc:
        print(f"ANVISA text smoke failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(run_smoke(storage_root=args.storage_root))


if __name__ == "__main__":
    main()
