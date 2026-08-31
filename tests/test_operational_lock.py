import pytest

from bulario_service.operational_lock import (
    OPERATIONAL_SYNC_LOCK_NAME,
    OperationalLockUnavailableError,
    operational_sync_lock,
)


class FakeConnection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def scalar(self, statement, params):
        self.calls.append((str(statement), params))
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class FakeEngine:
    def __init__(self, connection):
        self.connection = connection
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        return self.connection


def test_operational_lock_acquires_and_releases_on_same_connection() -> None:
    connection = FakeConnection([True, True])
    engine = FakeEngine(connection)

    with operational_sync_lock(engine, mode="incremental"):
        assert connection.closed is False
        assert len(connection.calls) == 1

    assert engine.connect_calls == 1
    assert connection.closed is True
    assert len(connection.calls) == 2
    assert "pg_try_advisory_lock" in connection.calls[0][0]
    assert "pg_advisory_unlock" in connection.calls[1][0]
    assert connection.calls[0][1]["lock_name"] == OPERATIONAL_SYNC_LOCK_NAME
    assert connection.calls[1][1]["lock_name"] == OPERATIONAL_SYNC_LOCK_NAME


def test_operational_lock_fails_fast_when_already_owned() -> None:
    connection = FakeConnection([False])
    engine = FakeEngine(connection)

    with pytest.raises(
        OperationalLockUnavailableError,
        match="already running",
    ):
        with operational_sync_lock(engine, mode="reconciliation"):
            pytest.fail("body must not run")

    assert connection.closed is True
    assert len(connection.calls) == 1


def test_operational_lock_releases_after_body_failure() -> None:
    connection = FakeConnection([True, True])
    engine = FakeEngine(connection)

    with pytest.raises(RuntimeError, match="controlled failure"):
        with operational_sync_lock(engine, mode="full"):
            raise RuntimeError("controlled failure")

    assert connection.closed is True
    assert len(connection.calls) == 2
    assert "pg_advisory_unlock" in connection.calls[1][0]
