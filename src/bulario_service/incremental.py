from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from bulario_service.models import IngestionRun


INCREMENTAL_MODE = "incremental"
COMPLETED_STATUS = "completed"


class IncrementalWindowError(ValueError):
    """Raised when an incremental window cannot be determined safely."""


@dataclass(frozen=True)
class IncrementalWindow:
    period_start: str
    period_end: str
    overlap_days: int
    based_on_run_id: int | None


def resolve_incremental_window(
    session: Session,
    *,
    overlap_days: int,
    period_end: str | None = None,
    initial_period_start: str | None = None,
    now: datetime | None = None,
) -> IncrementalWindow:
    if overlap_days < 1:
        raise IncrementalWindowError(
            "overlap_days must be greater than zero"
        )

    end_dt = _parse_source_datetime(period_end) if period_end else (
        now or datetime.now(UTC)
    )
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=UTC)
    else:
        end_dt = end_dt.astimezone(UTC)

    previous = latest_completed_incremental_run(session)
    based_on_run_id: int | None = None

    if previous is not None:
        if not previous.period_end:
            raise IncrementalWindowError(
                "latest completed incremental run has no period_end"
            )
        previous_end = _parse_source_datetime(previous.period_end)
        start_dt = previous_end - timedelta(days=overlap_days)
        based_on_run_id = previous.id
    else:
        if not initial_period_start:
            raise IncrementalWindowError(
                "initial_period_start is required when there is no completed "
                "incremental run"
            )
        start_dt = _parse_source_datetime(initial_period_start)

    if start_dt >= end_dt:
        raise IncrementalWindowError(
            "incremental period_start must be earlier than period_end"
        )

    return IncrementalWindow(
        period_start=_format_source_datetime(start_dt),
        period_end=_format_source_datetime(end_dt),
        overlap_days=overlap_days,
        based_on_run_id=based_on_run_id,
    )


def latest_completed_incremental_run(
    session: Session,
) -> IngestionRun | None:
    statement = (
        select(IngestionRun)
        .where(
            IngestionRun.mode == INCREMENTAL_MODE,
            IngestionRun.status == COMPLETED_STATUS,
        )
        .order_by(
            IngestionRun.finished_at.desc().nullslast(),
            IngestionRun.id.desc(),
        )
        .limit(1)
    )
    return session.scalars(statement).first()


def _parse_source_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise IncrementalWindowError(
            f"invalid ISO-8601 datetime: {value}"
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_source_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
