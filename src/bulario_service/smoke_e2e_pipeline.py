import argparse
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from bulario_service.anvisa import AnvisaBularioConnector, AnvisaSourceError
from bulario_service.anvisa_documents import AnvisaDocumentDownloader
from bulario_service.anvisa_session import (
    AnvisaAuthenticatedHttpClient,
    AnvisaBrowserSessionBootstrap,
)
from bulario_service.anvisa_transport_probe import DEFAULT_PROFILE_DIR
from bulario_service.config import load_settings
from bulario_service.db import create_database_engine
from bulario_service.document_storage import (
    DocumentStorageError,
    LocalDocumentStorage,
)
from bulario_service.document_text import (
    DocumentTextExtractionError,
    PdfTextExtractor,
)
from bulario_service.e2e_pipeline import (
    E2EPipelineError,
    run_single_product_pipeline,
)
from bulario_service.operational_persistence import OperationalPersistenceError
from bulario_service.publication_contract import BulaPublicationContractError
from bulario_service.publication_publisher import BulaPublicationError




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Executa o pipeline controlado completo para um produto real: "
            "ANVISA -> bulario.* -> public.bulas."
        )
    )
    parser.add_argument("--period-start", required=True)
    parser.add_argument("--period-end", required=True)
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Executa o bootstrap com Google Chrome visível.",
    )
    return parser


def run_smoke(
    *,
    period_start: str,
    period_end: str,
    profile_dir: Path,
    storage_root: Path | None,
    headed: bool,
) -> int:
    engine = None
    try:
        settings = load_settings()
        engine = create_database_engine(settings)
        effective_storage_root = storage_root or settings.storage_root

        bootstrap = AnvisaBrowserSessionBootstrap(
            profile_dir=profile_dir,
            headless=not headed,
        )
        session_state = bootstrap.bootstrap()
        print("Browser session bootstrap: OK")
        print("Browser closed. Starting controlled E2E...")

        storage = LocalDocumentStorage(effective_storage_root)
        extractor = PdfTextExtractor()

        with AnvisaAuthenticatedHttpClient(session_state) as authenticated:
            connector = AnvisaBularioConnector(
                client=authenticated.client,
            )
            downloader = AnvisaDocumentDownloader(
                authenticated.client,
            )

            with Session(engine) as db_session:
                result = run_single_product_pipeline(
                    db_session,
                    connector=connector,
                    downloader=downloader,
                    storage=storage,
                    extractor=extractor,
                    period_start=period_start,
                    period_end=period_end,
                )

        print(
            "E2E pipeline: OK "
            f"run_id={result.run_id} "
            f"item_id={result.item_id} "
            f"source_product_id={result.source_product_id} "
            f"source_document_id={result.source_document_id} "
            f"publish_action={result.publish_action} "
            f"public_row_id={result.public_row_id}"
        )
        print("run_status=completed")
        print("item_status=ready")
        return 0

    except (
        AnvisaSourceError,
        DocumentStorageError,
        DocumentTextExtractionError,
        OperationalPersistenceError,
        BulaPublicationContractError,
        BulaPublicationError,
        E2EPipelineError,
        RuntimeError,
    ) as exc:
        print(f"E2E pipeline failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(
        run_smoke(
            period_start=args.period_start,
            period_end=args.period_end,
            profile_dir=args.profile_dir,
            storage_root=args.storage_root,
            headed=args.headed,
        )
    )


if __name__ == "__main__":
    main()
