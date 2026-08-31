from argparse import Namespace
from pathlib import Path

from bulario_service.batch_ingestion import BatchIngestionResult
from bulario_service.sync import build_parser, run_cli


def result(*, status="paused", failed=0):
    return BatchIngestionResult(
        run_id=42,
        run_status=status,
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
