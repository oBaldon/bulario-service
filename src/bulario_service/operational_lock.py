from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection


OPERATIONAL_SYNC_LOCK_NAME = "bulario-service:sync:global:v1"


class OperationalLockError(RuntimeError):
    """Base error for operational synchronization locking."""


class OperationalLockUnavailableError(OperationalLockError):
    """Raised when another incompatible sync already owns the lock."""


@contextmanager
def operational_sync_lock(
    engine: Engine,
    *,
    mode: str,
) -> Iterator[None]:
    """
    Hold one PostgreSQL session-level advisory lock for the whole sync.

    The dedicated connection remains open across the many transactions used
    by the ingestion coordinator. All official operational modes currently
    share the same lock and are therefore mutually exclusive.
    """
    connection = engine.connect()
    acquired = False
    try:
        acquired = _try_acquire(connection)
        if not acquired:
            raise OperationalLockUnavailableError(
                "another incompatible bulario sync is already running "
                f"requested_mode={mode}"
            )
        yield
    finally:
        try:
            if acquired:
                _release(connection)
        finally:
            connection.close()


def _try_acquire(connection: Connection) -> bool:
    acquired = connection.scalar(
        text(
            """
            SELECT pg_try_advisory_lock(
                hashtextextended(:lock_name, 0)
            )
            """
        ),
        {"lock_name": OPERATIONAL_SYNC_LOCK_NAME},
    )
    return acquired is True


def _release(connection: Connection) -> None:
    released = connection.scalar(
        text(
            """
            SELECT pg_advisory_unlock(
                hashtextextended(:lock_name, 0)
            )
            """
        ),
        {"lock_name": OPERATIONAL_SYNC_LOCK_NAME},
    )
    if released is not True:
        raise OperationalLockError(
            "operational advisory lock was not owned during release"
        )
