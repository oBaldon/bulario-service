from argparse import Namespace
from pathlib import Path

from bulario_service.batch_ingestion import BatchIngestionResult
from bulario_service.sync import build_parser, run_cli


def result(*, status="paused", failed=0, mode="full"):
    return BatchIngestionResult(
        run_id=42,
        run_status=status,
        run_mode=mode,
        period_start="2026-01-01T00:00:00.000Z",
        period_end="2026-08-31T23:59:59.999Z",
        resumed=False,
        start_page=1,
        last_completed_page=2,
        pages_fetched=2,
        discovered_count=4,
        duplicate_count=0,
        skipped_terminal_count=0,
        processed_count=4,
        ready_count=4 - failed,
        failed_count=failed,
        stopped_by_page_limit=status == "paused",
        stopped_by_product_limit=False,
        stopped_by_source_blocked=False,
        retry_count=0,
        source_total_elements=100,
        invocation_duration_seconds=12.345,
        items=(),
    )


def test_full_parser_has_safe_operational_defaults() -> None:
    args = build_parser().parse_args([
        "full",
        "--period-start",
        "2026-01-01T00:00:00.000Z",
        "--period-end",
        "2026-08-31T23:59:59.999Z",
    ])

    assert args.command == "full"
    assert args.max_pages == 10
    assert args.max_products == 20
    assert args.page_size is None
    assert args.resume is None
    assert args.max_product_retries == 2
    assert args.retry_backoff_seconds == 2.0


def test_full_parser_accepts_resume_without_window() -> None:
    args = build_parser().parse_args([
        "full",
        "--resume",
        "42",
        "--max-pages",
        "5",
    ])

    assert args.resume == 42
    assert args.period_start is None
    assert args.period_end is None
    assert args.max_pages == 5


def test_new_full_run_requires_window_before_execution(
    monkeypatch,
    capsys,
) -> None:
    called = False

    def execute(**kwargs):
        nonlocal called
        called = True
        return result()

    monkeypatch.setattr(
        "bulario_service.sync.execute_full_sync",
        execute,
    )

    args = build_parser().parse_args(["full"])
    code = run_cli(args)

    assert code == 4
    assert called is False
    assert "required" in capsys.readouterr().err


def test_successful_paused_full_run_is_valid_cli_result(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "bulario_service.sync.execute_full_sync",
        lambda **kwargs: result(status="paused"),
    )

    args = build_parser().parse_args([
        "full",
        "--period-start",
        "2026-01-01T00:00:00.000Z",
        "--period-end",
        "2026-08-31T23:59:59.999Z",
        "--headed",
    ])
    code = run_cli(args)

    output = capsys.readouterr().out
    assert code == 0
    assert "run_status=paused" in output
    assert "source_total_elements=100" in output
    assert "duration_seconds=12.345" in output
    assert "full_sync_ready=true" in output


def test_full_run_with_failed_items_returns_operational_error(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "bulario_service.sync.execute_full_sync",
        lambda **kwargs: result(status="failed", failed=1),
    )

    args = build_parser().parse_args([
        "full",
        "--period-start",
        "2026-01-01T00:00:00.000Z",
        "--period-end",
        "2026-08-31T23:59:59.999Z",
    ])

    assert run_cli(args) == 2



def test_incremental_parser_has_safe_operational_defaults() -> None:
    args = build_parser().parse_args([
        "incremental",
        "--initial-period-start",
        "2026-08-29T00:00:00.000Z",
        "--period-end",
        "2026-08-31T00:00:00.000Z",
    ])

    assert args.command == "incremental"
    assert args.max_pages == 5
    assert args.max_products == 20
    assert args.page_size is None
    assert args.overlap_days is None
    assert args.resume is None
    assert args.max_product_retries == 2
    assert args.retry_backoff_seconds == 2.0


def test_incremental_parser_accepts_resume_without_window() -> None:
    args = build_parser().parse_args([
        "incremental",
        "--resume",
        "42",
        "--max-products",
        "10",
    ])

    assert args.resume == 42
    assert args.initial_period_start is None
    assert args.period_end is None
    assert args.max_products == 10


def test_incremental_cli_prints_resolved_window(
    monkeypatch,
    capsys,
) -> None:
    from bulario_service.incremental import IncrementalWindow

    window = IncrementalWindow(
        period_start="2026-08-24T00:00:00.000Z",
        period_end="2026-08-31T00:00:00.000Z",
        overlap_days=7,
        based_on_run_id=41,
    )

    monkeypatch.setattr(
        "bulario_service.sync.execute_incremental_sync",
        lambda **kwargs: (
            result(status="completed", mode="incremental"),
            window,
        ),
    )

    args = build_parser().parse_args([
        "incremental",
        "--period-end",
        "2026-08-31T00:00:00.000Z",
    ])
    code = run_cli(args)

    output = capsys.readouterr().out
    assert code == 0
    assert "Incremental window:" in output
    assert "overlap_days=7" in output
    assert "based_on_run_id=41" in output
    assert "incremental_sync_ready=true" in output


def test_incremental_cli_returns_error_when_window_cannot_be_resolved(
    monkeypatch,
    capsys,
) -> None:
    from bulario_service.incremental import IncrementalWindowError

    def fail(**kwargs):
        raise IncrementalWindowError("initial_period_start is required")

    monkeypatch.setattr(
        "bulario_service.sync.execute_incremental_sync",
        fail,
    )

    args = build_parser().parse_args(["incremental"])
    code = run_cli(args)

    assert code == 2
    assert "initial_period_start" in capsys.readouterr().err


def test_incremental_cli_accepts_paused_result(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "bulario_service.sync.execute_incremental_sync",
        lambda **kwargs: (
            result(status="paused", mode="incremental"),
            None,
        ),
    )

    args = build_parser().parse_args([
        "incremental",
        "--resume",
        "42",
    ])

    code = run_cli(args)
    output = capsys.readouterr().out
    assert code == 0
    assert "run_status=paused" in output
    assert "run_mode=incremental" in output
    assert "incremental_sync_ready=true" in output



def test_incremental_resume_rejects_window_overrides(
    monkeypatch,
    capsys,
) -> None:
    called = False

    def execute(**kwargs):
        nonlocal called
        called = True
        return result(mode="incremental"), None

    monkeypatch.setattr(
        "bulario_service.sync.execute_incremental_sync",
        execute,
    )

    args = build_parser().parse_args([
        "incremental",
        "--resume",
        "42",
        "--period-end",
        "2026-08-31T00:00:00.000Z",
    ])

    code = run_cli(args)

    assert code == 4
    assert called is False
    assert "janela persistida" in capsys.readouterr().err



def test_reconcile_parser_has_safe_operational_defaults() -> None:
    args = build_parser().parse_args([
        "reconcile",
        "--period-start",
        "2026-01-01T00:00:00.000Z",
        "--period-end",
        "2026-08-31T23:59:59.999Z",
    ])

    assert args.command == "reconcile"
    assert args.max_pages == 5
    assert args.max_products == 20
    assert args.page_size is None
    assert args.max_product_retries == 2
    assert args.retry_backoff_seconds == 2.0
    assert args.resume is None


def test_reconcile_parser_accepts_resume_without_window() -> None:
    args = build_parser().parse_args([
        "reconcile",
        "--resume",
        "42",
        "--max-products",
        "10",
    ])

    assert args.resume == 42
    assert args.period_start is None
    assert args.period_end is None
    assert args.max_products == 10


def test_new_reconciliation_requires_explicit_window(
    monkeypatch,
    capsys,
) -> None:
    called = False

    def execute(**kwargs):
        nonlocal called
        called = True
        return result(mode="reconciliation")

    monkeypatch.setattr(
        "bulario_service.sync.execute_reconciliation_sync",
        execute,
    )

    args = build_parser().parse_args(["reconcile"])
    code = run_cli(args)

    assert code == 4
    assert called is False
    assert "--period-start and --period-end" in capsys.readouterr().err


def test_reconciliation_cli_accepts_paused_result(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "bulario_service.sync.execute_reconciliation_sync",
        lambda **kwargs: result(
            status="paused",
            mode="reconciliation",
        ),
    )

    args = build_parser().parse_args([
        "reconcile",
        "--period-start",
        "2026-01-01T00:00:00.000Z",
        "--period-end",
        "2026-08-31T23:59:59.999Z",
    ])

    code = run_cli(args)
    output = capsys.readouterr().out
    assert code == 0
    assert "Reconciliation sync:" in output
    assert "run_status=paused" in output
    assert "run_mode=reconciliation" in output
    assert "reconciliation_sync_ready=true" in output


def test_reconciliation_cli_returns_error_with_failed_items(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "bulario_service.sync.execute_reconciliation_sync",
        lambda **kwargs: result(
            status="failed",
            failed=1,
            mode="reconciliation",
        ),
    )

    args = build_parser().parse_args([
        "reconcile",
        "--period-start",
        "2026-01-01T00:00:00.000Z",
        "--period-end",
        "2026-08-31T23:59:59.999Z",
    ])

    assert run_cli(args) == 2
