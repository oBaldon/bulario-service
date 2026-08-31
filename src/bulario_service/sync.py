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
    INCREMENTAL_MODE,
    RECONCILIATION_MODE,
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
from bulario_service.incremental import (
    IncrementalWindow,
    IncrementalWindowError,
    resolve_auto_resume_run_id,
    resolve_incremental_window,
)
from bulario_service.ingestion import recover_failed_ingestion_run
from bulario_service.models import IngestionRun
from bulario_service.operational_lock import (
    OperationalLockUnavailableError,
    operational_sync_lock,
)
from bulario_service.observability import (
    emit_observation,
    result_metrics,
    sanitize_observation,
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
        "--max-product-retries",
        type=int,
        default=2,
        help="Máximo de retries operacionais por produto no mesmo run.",
    )
    full.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=2.0,
        help="Espera entre retries operacionais de produto.",
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

    incremental = subparsers.add_parser(
        "incremental",
        help="Executa sincronização incremental com overlap configurável.",
    )
    incremental.add_argument(
        "--initial-period-start",
        default=None,
        help=(
            "Início usado somente quando ainda não existe incremental "
            "completed no banco."
        ),
    )
    incremental.add_argument(
        "--period-end",
        default=None,
        help=(
            "Fim da janela. Quando omitido em novo run, usa o instante UTC "
            "atual. Em resume, a janela persistida é reutilizada."
        ),
    )
    incremental.add_argument(
        "--resume",
        type=int,
        default=None,
        metavar="RUN_ID",
        help="Retoma um run incremental pausado.",
    )
    incremental.add_argument(
        "--auto-resume",
        action="store_true",
        help=(
            "Para scheduler: retoma automaticamente o único incremental "
            "pausado; se não houver, inicia uma nova janela."
        ),
    )
    incremental.add_argument(
        "--recover-failed",
        type=int,
        default=None,
        metavar="RUN_ID",
        help=(
            "Recuperação operacional explícita: reabre um incremental "
            "failed como paused e retoma o mesmo run."
        ),
    )
    incremental.add_argument(
        "--overlap-days",
        type=int,
        default=None,
        help=(
            "Sobreposição em dias. Quando omitido, usa "
            "BULARIO_INCREMENTAL_OVERLAP_DAYS."
        ),
    )
    incremental.add_argument(
        "--page-size",
        type=int,
        default=None,
        help=(
            "Quantidade solicitada por página. Novo run usa 10 quando "
            "omitido; resume reutiliza o valor persistido."
        ),
    )
    incremental.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Máximo de páginas consultadas nesta invocação.",
    )
    incremental.add_argument(
        "--max-products",
        type=int,
        default=20,
        help="Máximo de produtos processados nesta invocação.",
    )
    incremental.add_argument(
        "--max-product-retries",
        type=int,
        default=2,
        help="Máximo de retries operacionais por produto no mesmo run.",
    )
    incremental.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=2.0,
        help="Espera entre retries operacionais de produto.",
    )
    incremental.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
    )
    incremental.add_argument(
        "--storage-root",
        type=Path,
        default=None,
    )
    incremental.add_argument(
        "--headed",
        action="store_true",
        help="Executa o bootstrap com Google Chrome visível.",
    )

    reconcile = subparsers.add_parser(
        "reconcile",
        help="Executa uma varredura ampla de reconciliação.",
    )
    reconcile.add_argument("--period-start")
    reconcile.add_argument("--period-end")
    reconcile.add_argument(
        "--resume",
        type=int,
        default=None,
        metavar="RUN_ID",
        help="Retoma um run de reconciliação pausado.",
    )
    reconcile.add_argument(
        "--page-size",
        type=int,
        default=None,
        help=(
            "Quantidade solicitada por página. Novo run usa 10 quando "
            "omitido; resume reutiliza o valor persistido."
        ),
    )
    reconcile.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Máximo de páginas consultadas nesta invocação.",
    )
    reconcile.add_argument(
        "--max-products",
        type=int,
        default=20,
        help="Máximo de produtos processados nesta invocação.",
    )
    reconcile.add_argument(
        "--max-product-retries",
        type=int,
        default=2,
        help="Máximo de retries operacionais por produto no mesmo run.",
    )
    reconcile.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=2.0,
        help="Espera entre retries operacionais de produto.",
    )
    reconcile.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
    )
    reconcile.add_argument(
        "--storage-root",
        type=Path,
        default=None,
    )
    reconcile.add_argument(
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
    max_product_retries: int,
    retry_backoff_seconds: float,
    profile_dir: Path,
    storage_root: Path | None,
    headed: bool,
) -> BatchIngestionResult:
    settings = load_settings()
    engine = create_database_engine(settings)
    try:
        with operational_sync_lock(engine, mode=FULL_MODE):
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
                        max_product_retries=max_product_retries,
                        retry_backoff_seconds=retry_backoff_seconds,
                    )
    finally:
        engine.dispose()


def execute_incremental_sync(
    *,
    initial_period_start: str | None,
    period_end: str | None,
    resume_run_id: int | None,
    auto_resume: bool,
    recover_failed_run_id: int | None,
    overlap_days: int | None,
    page_size: int | None,
    max_pages: int | None,
    max_products: int | None,
    max_product_retries: int,
    retry_backoff_seconds: float,
    profile_dir: Path,
    storage_root: Path | None,
    headed: bool,
) -> tuple[BatchIngestionResult, IncrementalWindow | None]:
    settings = load_settings()
    engine = create_database_engine(settings)
    try:
        with operational_sync_lock(engine, mode=INCREMENTAL_MODE):
            effective_storage_root = storage_root or settings.storage_root
            effective_resume_run_id = resume_run_id
            if recover_failed_run_id is not None:
                with Session(engine) as recovery_session:
                    failed_run = recovery_session.get(
                        IngestionRun,
                        recover_failed_run_id,
                    )
                    if failed_run is None:
                        raise IncrementalWindowError(
                            "cannot recover unknown incremental run "
                            f"run_id={recover_failed_run_id}"
                        )
                    if failed_run.mode != INCREMENTAL_MODE:
                        raise IncrementalWindowError(
                            "failed run is not incremental "
                            f"run_id={recover_failed_run_id} "
                            f"mode={failed_run.mode}"
                        )
                    recover_failed_ingestion_run(
                        recovery_session,
                        failed_run,
                    )
                    recovery_session.commit()
                effective_resume_run_id = recover_failed_run_id

            if auto_resume and effective_resume_run_id is None:
                with Session(engine) as resume_session:
                    effective_resume_run_id = resolve_auto_resume_run_id(
                        resume_session
                    )

            effective_overlap = (
                overlap_days
                if overlap_days is not None
                else settings.incremental_overlap_days
            )

            if effective_resume_run_id is None:
                with Session(engine) as window_session:
                    window = resolve_incremental_window(
                        window_session,
                        overlap_days=effective_overlap,
                        period_end=period_end,
                        initial_period_start=initial_period_start,
                    )
                resolved_start = window.period_start
                resolved_end = window.period_end
            else:
                window = None
                resolved_start = None
                resolved_end = None

            bootstrap = AnvisaBrowserSessionBootstrap(
                profile_dir=profile_dir,
                headless=not headed,
            )
            session_state = bootstrap.bootstrap()
            print("Browser session bootstrap: OK")
            print("Browser closed. Starting incremental sync...")

            storage = LocalDocumentStorage(effective_storage_root)
            extractor = PdfTextExtractor()

            with AnvisaAuthenticatedHttpClient(session_state) as authenticated:
                connector = AnvisaBularioConnector(client=authenticated.client)
                downloader = AnvisaDocumentDownloader(authenticated.client)

                with Session(engine) as db_session:
                    result = run_batch_ingestion(
                        db_session,
                        connector=connector,
                        downloader=downloader,
                        storage=storage,
                        extractor=extractor,
                        period_start=resolved_start,
                        period_end=resolved_end,
                        page_size=page_size,
                        max_pages=max_pages,
                        max_products=max_products,
                        resume_run_id=effective_resume_run_id,
                        run_mode=INCREMENTAL_MODE,
                        max_product_retries=max_product_retries,
                        retry_backoff_seconds=retry_backoff_seconds,
                    )
                    return result, window
    finally:
        engine.dispose()



def execute_reconciliation_sync(
    *,
    period_start: str | None,
    period_end: str | None,
    resume_run_id: int | None,
    page_size: int | None,
    max_pages: int | None,
    max_products: int | None,
    max_product_retries: int,
    retry_backoff_seconds: float,
    profile_dir: Path,
    storage_root: Path | None,
    headed: bool,
) -> BatchIngestionResult:
    settings = load_settings()
    engine = create_database_engine(settings)
    try:
        with operational_sync_lock(engine, mode=RECONCILIATION_MODE):
            effective_storage_root = storage_root or settings.storage_root

            bootstrap = AnvisaBrowserSessionBootstrap(
                profile_dir=profile_dir,
                headless=not headed,
            )
            session_state = bootstrap.bootstrap()
            print("Browser session bootstrap: OK")
            print("Browser closed. Starting reconciliation sync...")

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
                        run_mode=RECONCILIATION_MODE,
                        max_product_retries=max_product_retries,
                        retry_backoff_seconds=retry_backoff_seconds,
                    )
    finally:
        engine.dispose()


def print_reconciliation_result(result: BatchIngestionResult) -> None:
    print(
        "Reconciliation sync: "
        f"run_id={result.run_id} "
        f"run_status={result.run_status} "
        f"run_mode={result.run_mode} "
        f"period_start={result.period_start} "
        f"period_end={result.period_end} "
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
        f"retries={result.retry_count} "
        f"duration_seconds={result.invocation_duration_seconds:.3f} "
        f"stopped_by_page_limit="
        f"{str(result.stopped_by_page_limit).lower()} "
        f"stopped_by_product_limit="
        f"{str(result.stopped_by_product_limit).lower()} "
        f"stopped_by_source_blocked="
        f"{str(result.stopped_by_source_blocked).lower()}"
    )

    for item in result.items:
        print(
            "Reconciliation item "
            f"source_product_id={item.source_product_id} "
            f"status={item.status} "
            f"item_id={item.item_id or '-'} "
            f"source_document_id={item.source_document_id or '-'} "
            f"publish_action={item.publish_action or '-'} "
            f"public_row_id={item.public_row_id or '-'} "
            f"error_code={item.error_code or '-'} "
            f"error_class={item.error_class or '-'} "
            f"retries={item.retry_count}"
        )


def print_incremental_result(
    result: BatchIngestionResult,
    *,
    window: IncrementalWindow | None,
) -> None:
    if window is not None:
        print(
            "Incremental window: "
            f"period_start={window.period_start} "
            f"period_end={window.period_end} "
            f"overlap_days={window.overlap_days} "
            f"based_on_run_id={window.based_on_run_id or '-'}"
        )

    print(
        "Incremental sync: "
        f"run_id={result.run_id} "
        f"run_status={result.run_status} "
        f"run_mode={result.run_mode} "
        f"period_start={result.period_start} "
        f"period_end={result.period_end} "
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
        f"retries={result.retry_count} "
        f"duration_seconds={result.invocation_duration_seconds:.3f} "
        f"stopped_by_page_limit="
        f"{str(result.stopped_by_page_limit).lower()} "
        f"stopped_by_product_limit="
        f"{str(result.stopped_by_product_limit).lower()} "
        f"stopped_by_source_blocked="
        f"{str(result.stopped_by_source_blocked).lower()}"
    )

    for item in result.items:
        print(
            "Incremental item "
            f"source_product_id={item.source_product_id} "
            f"status={item.status} "
            f"item_id={item.item_id or '-'} "
            f"source_document_id={item.source_document_id or '-'} "
            f"publish_action={item.publish_action or '-'} "
            f"public_row_id={item.public_row_id or '-'} "
            f"error_code={item.error_code or '-'} "
            f"error_class={item.error_class or '-'} "
            f"retries={item.retry_count}"
        )



def print_result(result: BatchIngestionResult) -> None:
    print(
        "Full sync: "
        f"run_id={result.run_id} "
        f"run_status={result.run_status} "
        f"run_mode={result.run_mode} "
        f"period_start={result.period_start} "
        f"period_end={result.period_end} "
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
        f"retries={result.retry_count} "
        f"duration_seconds={result.invocation_duration_seconds:.3f} "
        f"stopped_by_page_limit="
        f"{str(result.stopped_by_page_limit).lower()} "
        f"stopped_by_product_limit="
        f"{str(result.stopped_by_product_limit).lower()} "
        f"stopped_by_source_blocked="
        f"{str(result.stopped_by_source_blocked).lower()}"
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
            f"error_code={item.error_code or '-'} "
            f"error_class={item.error_class or '-'} "
            f"retries={item.retry_count}"
        )


def _safe_error_message(error: Exception) -> str:
    sanitized = sanitize_observation(str(error))
    return str(sanitized)


def _emit_sync_started(args: argparse.Namespace) -> None:
    emit_observation(
        "sync_started",
        mode=args.command,
        resume_run_id=getattr(args, "resume", None),
        requested_period_start=(
            getattr(args, "period_start", None)
            or getattr(args, "initial_period_start", None)
        ),
        requested_period_end=getattr(args, "period_end", None),
        page_size=getattr(args, "page_size", None),
        max_pages=getattr(args, "max_pages", None),
        max_products=getattr(args, "max_products", None),
        max_product_retries=getattr(
            args,
            "max_product_retries",
            None,
        ),
        auto_resume=getattr(args, "auto_resume", False),
        recover_failed_run_id=getattr(
            args,
            "recover_failed",
            None,
        ),
    )


def _emit_sync_result(result: BatchIngestionResult) -> None:
    emit_observation(
        "sync_result",
        **result_metrics(result),
    )


def _emit_sync_blocked(
    *,
    mode: str,
    error: Exception,
) -> None:
    emit_observation(
        "sync_blocked",
        mode=mode,
        error_type=type(error).__name__,
        error_message=_safe_error_message(error),
        exit_code=3,
    )


def _emit_sync_failed(
    *,
    mode: str,
    error: Exception,
    exit_code: int,
) -> None:
    emit_observation(
        "sync_failed",
        mode=mode,
        error_type=type(error).__name__,
        error_message=_safe_error_message(error),
        exit_code=exit_code,
    )


def _emit_invalid_request(
    *,
    mode: str,
    message: str,
) -> None:
    emit_observation(
        "sync_invalid_request",
        mode=mode,
        error_message=message,
        exit_code=4,
    )


def run_cli(args: argparse.Namespace) -> int:
    if args.command == "full":
        if args.resume is None and (
            not args.period_start or not args.period_end
        ):
            message = (
                "--period-start and --period-end are required "
                "for a new full run."
            )
            _emit_invalid_request(mode="full", message=message)
            print(f"Full sync failed: {message}", file=sys.stderr)
            return 4

        _emit_sync_started(args)
        try:
            result = execute_full_sync(
                period_start=args.period_start,
                period_end=args.period_end,
                resume_run_id=args.resume,
                page_size=args.page_size,
                max_pages=args.max_pages,
                max_products=args.max_products,
                max_product_retries=args.max_product_retries,
                retry_backoff_seconds=args.retry_backoff_seconds,
                profile_dir=args.profile_dir,
                storage_root=args.storage_root,
                headed=args.headed,
            )
        except OperationalLockUnavailableError as exc:
            _emit_sync_blocked(mode="full", error=exc)
            print(f"Full sync blocked: {_safe_error_message(exc)}", file=sys.stderr)
            return 3
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
            _emit_sync_failed(mode="full", error=exc, exit_code=2)
            print(f"Full sync failed: {_safe_error_message(exc)}", file=sys.stderr)
            return 2

        _emit_sync_result(result)
        print_result(result)
        if result.failed_count:
            return 2
        print("full_sync_ready=true")
        return 0

    if args.command == "incremental":
        selected_resume_modes = sum((
            args.resume is not None,
            args.auto_resume,
            args.recover_failed is not None,
        ))
        if selected_resume_modes > 1:
            message = (
                "--resume, --auto-resume e --recover-failed são "
                "mutuamente exclusivos."
            )
            _emit_invalid_request(
                mode="incremental",
                message=message,
            )
            print(f"Incremental sync failed: {message}", file=sys.stderr)
            return 4

        if args.resume is not None and any((
            args.initial_period_start is not None,
            args.period_end is not None,
            args.overlap_days is not None,
        )):
            message = (
                "resume reutiliza a janela persistida; não informe "
                "--initial-period-start, --period-end ou --overlap-days."
            )
            _emit_invalid_request(
                mode="incremental",
                message=message,
            )
            print(
                f"Incremental sync failed: {message}",
                file=sys.stderr,
            )
            return 4

        if (
            args.auto_resume or args.recover_failed is not None
        ) and any((
            args.initial_period_start is not None,
            args.period_end is not None,
            args.overlap_days is not None,
        )):
            message = (
                "resume automático/recuperação reutiliza a janela "
                "persistida; não combine com overrides de janela."
            )
            _emit_invalid_request(
                mode="incremental",
                message=message,
            )
            print(f"Incremental sync failed: {message}", file=sys.stderr)
            return 4

        _emit_sync_started(args)
        try:
            result, window = execute_incremental_sync(
                initial_period_start=args.initial_period_start,
                period_end=args.period_end,
                resume_run_id=args.resume,
                auto_resume=args.auto_resume,
                recover_failed_run_id=args.recover_failed,
                overlap_days=args.overlap_days,
                page_size=args.page_size,
                max_pages=args.max_pages,
                max_products=args.max_products,
                max_product_retries=args.max_product_retries,
                retry_backoff_seconds=args.retry_backoff_seconds,
                profile_dir=args.profile_dir,
                storage_root=args.storage_root,
                headed=args.headed,
            )
        except OperationalLockUnavailableError as exc:
            _emit_sync_blocked(mode="incremental", error=exc)
            print(f"Incremental sync blocked: {_safe_error_message(exc)}", file=sys.stderr)
            return 3
        except (
            AnvisaSourceError,
            DocumentStorageError,
            DocumentTextExtractionError,
            OperationalPersistenceError,
            BulaPublicationContractError,
            BulaPublicationError,
            BatchIngestionError,
            IncrementalWindowError,
            RuntimeError,
            ValueError,
        ) as exc:
            _emit_sync_failed(
                mode="incremental",
                error=exc,
                exit_code=2,
            )
            print(f"Incremental sync failed: {_safe_error_message(exc)}", file=sys.stderr)
            return 2

        _emit_sync_result(result)
        print_incremental_result(result, window=window)
        if result.failed_count:
            return 2
        print("incremental_sync_ready=true")
        return 0

    if args.command == "reconcile":
        if args.resume is None and (
            not args.period_start or not args.period_end
        ):
            message = (
                "--period-start and --period-end are required "
                "for a new reconciliation run."
            )
            _emit_invalid_request(
                mode="reconcile",
                message=message,
            )
            print(
                f"Reconciliation sync failed: {message}",
                file=sys.stderr,
            )
            return 4

        _emit_sync_started(args)
        try:
            result = execute_reconciliation_sync(
                period_start=args.period_start,
                period_end=args.period_end,
                resume_run_id=args.resume,
                page_size=args.page_size,
                max_pages=args.max_pages,
                max_products=args.max_products,
                max_product_retries=args.max_product_retries,
                retry_backoff_seconds=args.retry_backoff_seconds,
                profile_dir=args.profile_dir,
                storage_root=args.storage_root,
                headed=args.headed,
            )
        except OperationalLockUnavailableError as exc:
            _emit_sync_blocked(
                mode="reconciliation",
                error=exc,
            )
            print(f"Reconciliation sync blocked: {_safe_error_message(exc)}", file=sys.stderr)
            return 3
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
            _emit_sync_failed(
                mode="reconciliation",
                error=exc,
                exit_code=2,
            )
            print(f"Reconciliation sync failed: {_safe_error_message(exc)}", file=sys.stderr)
            return 2

        _emit_sync_result(result)
        print_reconciliation_result(result)
        if result.failed_count:
            return 2
        print("reconciliation_sync_ready=true")
        return 0

    raise ValueError(f"unsupported command: {args.command}")

def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(run_cli(args))


if __name__ == "__main__":
    main()
