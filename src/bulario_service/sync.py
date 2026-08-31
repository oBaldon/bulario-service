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
    FULL_MODE,
    BatchIngestionError,
    BatchIngestionResult,
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
        prog="python -m bulario_service.sync",
        description="Interface operacional do bulario-service.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    full = subparsers.add_parser(
        "full",
        help="Executa uma carga full controlada e resumível.",
    )
    full.add_argument("--period-start")
    full.add_argument("--period-end")
    full.add_argument(
        "--resume",
        type=int,
        default=None,
        metavar="RUN_ID",
        help="Retoma um run full pausado.",
    )
    full.add_argument(
        "--page-size",
        type=int,
        default=None,
        help=(
            "Quantidade solicitada por página. Novo run usa 10 quando "
            "omitido; resume reutiliza o valor persistido."
        ),
    )
    full.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="Máximo de páginas consultadas nesta invocação.",
    )
    full.add_argument(
        "--max-products",
        type=int,
        default=20,
        help="Máximo de produtos processados nesta invocação.",
    )
    full.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
    )
    full.add_argument(
        "--storage-root",
        type=Path,
        default=None,
    )
    full.add_argument(
        "--headed",
        action="store_true",
        help="Executa o bootstrap com Google Chrome visível.",
    )
    return parser


def execute_full_sync(
    *,
    period_start: str | None,
    period_end: str | None,
    resume_run_id: int | None,
    page_size: int | None,
    max_pages: int | None,
    max_products: int | None,
    profile_dir: Path,
    storage_root: Path | None,
    headed: bool,
) -> BatchIngestionResult:
    settings = load_settings()
    engine = create_database_engine(settings)
    try:
        effective_storage_root = storage_root or settings.storage_root

        bootstrap = AnvisaBrowserSessionBootstrap(
            profile_dir=profile_dir,
            headless=not headed,
        )
        session_state = bootstrap.bootstrap()
        print("Browser session bootstrap: OK")
        print("Browser closed. Starting full sync...")

        storage = LocalDocumentStorage(effective_storage_root)
        extractor = PdfTextExtractor()

        with AnvisaAuthenticatedHttpClient(session_state) as authenticated:
            connector = AnvisaBularioConnector(client=authenticated.client)
            downloader = AnvisaDocumentDownloader(authenticated.client)

            with Session(engine) as db_session:
                return run_batch_ingestion(
                    db_session,
                    connector=connector,
                    downloader=downloader,
                    storage=storage,
                    extractor=extractor,
                    period_start=period_start,
                    period_end=period_end,
                    page_size=page_size,
                    max_pages=max_pages,
                    max_products=max_products,
                    resume_run_id=resume_run_id,
                    run_mode=FULL_MODE,
                )
    finally:
        engine.dispose()


def print_result(result: BatchIngestionResult) -> None:
    print(
        "Full sync: "
        f"run_id={result.run_id} "
        f"run_status={result.run_status} "
        f"resumed={str(result.resumed).lower()} "
        f"start_page={result.start_page} "
        f"last_completed_page={result.last_completed_page} "
        f"pages_fetched={result.pages_fetched} "
        f"source_total_elements={result.source_total_elements or 0} "
        f"discovered={result.discovered_count} "
        f"duplicates={result.duplicate_count} "
        f"skipped_terminal={result.skipped_terminal_count} "
        f"processed={result.processed_count} "
        f"ready={result.ready_count} "
        f"failed={result.failed_count} "
        f"duration_seconds={result.invocation_duration_seconds:.3f} "
        f"stopped_by_page_limit="
        f"{str(result.stopped_by_page_limit).lower()} "
        f"stopped_by_product_limit="
        f"{str(result.stopped_by_product_limit).lower()}"
    )

    for item in result.items:
        print(
            "Full item "
            f"source_product_id={item.source_product_id} "
            f"status={item.status} "
            f"item_id={item.item_id or '-'} "
            f"source_document_id={item.source_document_id or '-'} "
            f"publish_action={item.publish_action or '-'} "
            f"public_row_id={item.public_row_id or '-'} "
            f"error_code={item.error_code or '-'}"
        )


def run_cli(args: argparse.Namespace) -> int:
    if args.command != "full":
        raise ValueError(f"unsupported command: {args.command}")

    if args.resume is None and (
        not args.period_start or not args.period_end
    ):
        print(
            "Full sync failed: --period-start and --period-end are required "
            "for a new full run.",
            file=sys.stderr,
        )
        return 4

    try:
        result = execute_full_sync(
            period_start=args.period_start,
            period_end=args.period_end,
            resume_run_id=args.resume,
            page_size=args.page_size,
            max_pages=args.max_pages,
            max_products=args.max_products,
            profile_dir=args.profile_dir,
            storage_root=args.storage_root,
            headed=args.headed,
        )
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
        print(f"Full sync failed: {exc}", file=sys.stderr)
        return 2

    print_result(result)

    if result.failed_count:
        return 2

    print("full_sync_ready=true")
    return 0


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(run_cli(args))


if __name__ == "__main__":
    main()
