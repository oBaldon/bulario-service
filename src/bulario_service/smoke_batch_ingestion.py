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
from bulario_service.batch_ingestion import (
    BatchIngestionError,
    run_batch_ingestion,
)
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
from bulario_service.operational_persistence import OperationalPersistenceError
from bulario_service.publication_contract import BulaPublicationContractError
from bulario_service.publication_publisher import BulaPublicationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Executa o Batch Ingestion Coordinator da Sprint 02 sobre a "
            "primeira página de discovery, processando múltiplos produtos "
            "no mesmo ingestion run."
        )
    )
    parser.add_argument("--period-start", required=True)
    parser.add_argument("--period-end", required=True)
    parser.add_argument(
        "--page-size",
        type=int,
        default=2,
        help=(
            "Quantidade solicitada na primeira página. Nesta etapa ainda "
            "não há paginação multi-page."
        ),
    )
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
    page_size: int,
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
        print("Browser closed. Starting batch ingestion smoke...")

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
                result = run_batch_ingestion(
                    db_session,
                    connector=connector,
                    downloader=downloader,
                    storage=storage,
                    extractor=extractor,
                    period_start=period_start,
                    period_end=period_end,
                    page_size=page_size,
                )

        print(
            "Batch ingestion: "
            f"run_id={result.run_id} "
            f"run_status={result.run_status} "
            f"discovered={result.discovered_count} "
            f"processed={result.processed_count} "
            f"ready={result.ready_count} "
            f"failed={result.failed_count}"
        )

        for item in result.items:
            print(
                "Batch item "
                f"source_product_id={item.source_product_id} "
                f"status={item.status} "
                f"item_id={item.item_id or '-'} "
                f"source_document_id={item.source_document_id or '-'} "
                f"publish_action={item.publish_action or '-'} "
                f"public_row_id={item.public_row_id or '-'} "
                f"error_code={item.error_code or '-'}"
            )

        if result.failed_count:
            print(
                "batch_ingestion_ready=false",
                file=sys.stderr,
            )
            return 2

        print("batch_ingestion_ready=true")
        return 0

    except (
        AnvisaSourceError,
        DocumentStorageError,
        DocumentTextExtractionError,
        OperationalPersistenceError,
        BulaPublicationContractError,
        BulaPublicationError,
        BatchIngestionError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"Batch ingestion smoke failed: {exc}", file=sys.stderr)
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
            page_size=args.page_size,
            profile_dir=args.profile_dir,
            storage_root=args.storage_root,
            headed=args.headed,
        )
    )


if __name__ == "__main__":
    main()
