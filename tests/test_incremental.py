from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bulario_service.models import IngestionRun

from bulario_service.incremental import (
    IncrementalWindowError,
    resolve_auto_resume_run_id,
    resolve_incremental_window,
)


class DummySession:
    pass


def test_first_incremental_requires_explicit_initial_start(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "bulario_service.incremental.latest_completed_incremental_run",
        lambda session: None,
    )

    with pytest.raises(
        IncrementalWindowError,
        match="initial_period_start",
    ):
        resolve_incremental_window(
            DummySession(),
            overlap_days=7,
            period_end="2026-08-31T12:00:00.000Z",
        )


def test_first_incremental_uses_explicit_initial_window(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "bulario_service.incremental.latest_completed_incremental_run",
        lambda session: None,
    )

    window = resolve_incremental_window(
        DummySession(),
        overlap_days=7,
        initial_period_start="2026-08-29T00:00:00Z",
        period_end="2026-08-31T12:00:00Z",
    )

    assert window.period_start == "2026-08-29T00:00:00.000Z"
    assert window.period_end == "2026-08-31T12:00:00.000Z"
    assert window.overlap_days == 7
    assert window.based_on_run_id is None


def test_next_incremental_starts_from_previous_end_minus_overlap(
    monkeypatch,
) -> None:
    previous = SimpleNamespace(
        id=41,
        period_end="2026-08-30T18:00:00.000Z",
    )
    monkeypatch.setattr(
        "bulario_service.incremental.latest_completed_incremental_run",
        lambda session: previous,
    )

    window = resolve_incremental_window(
        DummySession(),
        overlap_days=7,
        period_end="2026-08-31T18:00:00.000Z",
    )

    assert window.period_start == "2026-08-23T18:00:00.000Z"
    assert window.period_end == "2026-08-31T18:00:00.000Z"
    assert window.based_on_run_id == 41


def test_previous_completed_run_takes_precedence_over_initial_start(
    monkeypatch,
) -> None:
    previous = SimpleNamespace(
        id=8,
        period_end="2026-08-20T00:00:00.000Z",
    )
    monkeypatch.setattr(
        "bulario_service.incremental.latest_completed_incremental_run",
        lambda session: previous,
    )

    window = resolve_incremental_window(
        DummySession(),
        overlap_days=3,
        initial_period_start="2020-01-01T00:00:00.000Z",
        period_end="2026-08-21T00:00:00.000Z",
    )

    assert window.period_start == "2026-08-17T00:00:00.000Z"
    assert window.based_on_run_id == 8


def test_incremental_can_use_current_utc_time_deterministically(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "bulario_service.incremental.latest_completed_incremental_run",
        lambda session: None,
    )

    window = resolve_incremental_window(
        DummySession(),
        overlap_days=7,
        initial_period_start="2026-08-30T00:00:00.000Z",
        now=datetime(2026, 8, 31, 15, 30, tzinfo=UTC),
    )

    assert window.period_end == "2026-08-31T15:30:00.000Z"


@pytest.mark.parametrize("overlap", [0, -1])
def test_overlap_must_be_positive(monkeypatch, overlap) -> None:
    monkeypatch.setattr(
        "bulario_service.incremental.latest_completed_incremental_run",
        lambda session: None,
    )

    with pytest.raises(IncrementalWindowError, match="overlap_days"):
        resolve_incremental_window(
            DummySession(),
            overlap_days=overlap,
            initial_period_start="2026-08-30T00:00:00.000Z",
            period_end="2026-08-31T00:00:00.000Z",
        )


def test_window_rejects_start_not_before_end(monkeypatch) -> None:
    monkeypatch.setattr(
        "bulario_service.incremental.latest_completed_incremental_run",
        lambda session: None,
    )

    with pytest.raises(IncrementalWindowError, match="earlier"):
        resolve_incremental_window(
            DummySession(),
            overlap_days=7,
            initial_period_start="2026-08-31T00:00:00.000Z",
            period_end="2026-08-31T00:00:00.000Z",
        )



def test_auto_resume_returns_only_paused_incremental_run() -> None:
    session = Mock()
    run = IngestionRun(
        id=88,
        status="paused",
        mode="incremental",
    )
    session.scalars.return_value = [run]

    assert resolve_auto_resume_run_id(session) == 88


def test_auto_resume_returns_none_without_paused_incremental() -> None:
    session = Mock()
    session.scalars.return_value = []

    assert resolve_auto_resume_run_id(session) is None


def test_auto_resume_rejects_ambiguous_paused_incrementals() -> None:
    session = Mock()
    session.scalars.return_value = [
        IngestionRun(id=88, status="paused", mode="incremental"),
        IngestionRun(id=87, status="paused", mode="incremental"),
    ]

    with pytest.raises(
        IncrementalWindowError,
        match="multiple paused incremental runs",
    ):
        resolve_auto_resume_run_id(session)



def test_auto_resume_blocks_latest_failed_incremental() -> None:
    session = Mock()
    session.scalars.return_value = [
        IngestionRun(id=8, status="failed", mode="incremental"),
    ]

    with pytest.raises(
        IncrementalWindowError,
        match="explicit operator recovery",
    ):
        resolve_auto_resume_run_id(session)


def test_auto_resume_blocks_stale_running_incremental() -> None:
    session = Mock()
    session.scalars.return_value = [
        IngestionRun(id=8, status="running", mode="incremental"),
    ]

    with pytest.raises(
        IncrementalWindowError,
        match="already marked running",
    ):
        resolve_auto_resume_run_id(session)
