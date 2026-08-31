from types import SimpleNamespace

from bulario_service.operational_lock import (
    OperationalLockUnavailableError,
)
from bulario_service.smoke_operational_lock import run


class FakeEngine:
    def __init__(self):
        self.dispose_calls = 0

    def dispose(self):
        self.dispose_calls += 1


def test_lock_smoke_acquires_waits_and_releases(
    monkeypatch,
    capsys,
) -> None:
    engine = FakeEngine()
    events = []

    class Lock:
        def __enter__(self):
            events.append("acquire")

        def __exit__(self, exc_type, exc, tb):
            events.append("release")

    monkeypatch.setattr(
        "bulario_service.smoke_operational_lock.load_settings",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "bulario_service.smoke_operational_lock.create_database_engine",
        lambda settings: engine,
    )
    monkeypatch.setattr(
        "bulario_service.smoke_operational_lock.operational_sync_lock",
        lambda engine, mode: Lock(),
    )
    monkeypatch.setattr(
        "bulario_service.smoke_operational_lock.time.sleep",
        lambda seconds: events.append(("sleep", seconds)),
    )

    assert run(hold_seconds=3) == 0
    assert events == ["acquire", ("sleep", 3), "release"]
    assert engine.dispose_calls == 1
    output = capsys.readouterr().out
    assert "operational_lock_acquired=true" in output
    assert "operational_lock_released=true" in output


def test_lock_smoke_returns_three_when_lock_is_busy(
    monkeypatch,
    capsys,
) -> None:
    engine = FakeEngine()

    class BusyLock:
        def __enter__(self):
            raise OperationalLockUnavailableError("already running")

        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(
        "bulario_service.smoke_operational_lock.load_settings",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "bulario_service.smoke_operational_lock.create_database_engine",
        lambda settings: engine,
    )
    monkeypatch.setattr(
        "bulario_service.smoke_operational_lock.operational_sync_lock",
        lambda engine, mode: BusyLock(),
    )

    assert run(hold_seconds=0) == 3
    assert engine.dispose_calls == 1
    assert "operational_lock_acquired=false" in capsys.readouterr().out
