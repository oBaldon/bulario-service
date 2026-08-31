from __future__ import annotations

from datetime import UTC, datetime
import json
import re
import sys
from typing import Any, TextIO

from bulario_service.batch_ingestion import BatchIngestionResult


SENSITIVE_FIELD_MARKERS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
)

_REDACTION_PATTERNS = (
    re.compile(
        r"(?i)(authorization\s*[:=]\s*)([^\s&,;]+)"
    ),
    re.compile(
        r"(?i)(bearer\s+)([A-Za-z0-9._~+/-]+=*)"
    ),
    re.compile(
        r"(?i)(cookie\s*[:=]\s*)([^\r\n]+)"
    ),
    re.compile(
        r"(?i)(token\s*[:=]\s*)([^\s&,;]+)"
    ),
)


def emit_observation(
    event: str,
    *,
    stream: TextIO | None = None,
    **fields: Any,
) -> None:
    target = stream or sys.stderr
    payload = {
        "timestamp": datetime.now(UTC).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z"),
        "service": "bulario-service",
        "event": event,
        **sanitize_observation(fields),
    }
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        file=target,
        flush=True,
    )


def sanitize_observation(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return "[REDACTED]"

    if isinstance(value, dict):
        return {
            str(item_key): sanitize_observation(
                item_value,
                key=str(item_key),
            )
            for item_key, item_value in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [sanitize_observation(item) for item in value]

    if isinstance(value, str):
        return _sanitize_text(value)

    if value is None or isinstance(value, (bool, int, float)):
        return value

    return _sanitize_text(str(value))


def result_metrics(result: BatchIngestionResult) -> dict[str, Any]:
    inserted = sum(
        1 for item in result.items
        if item.publish_action == "inserted"
    )
    unchanged = sum(
        1 for item in result.items
        if item.publish_action == "unchanged"
    )
    conflicts = sum(
        1 for item in result.items
        if item.error_class == "conflict"
    )
    failed_by_class: dict[str, int] = {}
    for item in result.items:
        if item.status != "failed":
            continue
        error_class = item.error_class or "unknown"
        failed_by_class[error_class] = (
            failed_by_class.get(error_class, 0) + 1
        )

    return {
        "run_id": result.run_id,
        "run_status": result.run_status,
        "mode": result.run_mode,
        "period_start": result.period_start,
        "period_end": result.period_end,
        "resumed": result.resumed,
        "start_page": result.start_page,
        "checkpoint_page": result.last_completed_page,
        "pages_fetched": result.pages_fetched,
        "source_total_elements": result.source_total_elements,
        "discovered": result.discovered_count,
        "duplicates": result.duplicate_count,
        "skipped_terminal": result.skipped_terminal_count,
        "processed": result.processed_count,
        "ready": result.ready_count,
        "failed": result.failed_count,
        "published": inserted,
        "inserted": inserted,
        "unchanged": unchanged,
        "conflicts": conflicts,
        "retries": result.retry_count,
        "failed_by_class": failed_by_class,
        "duration_seconds": result.invocation_duration_seconds,
        "stopped_by_page_limit": result.stopped_by_page_limit,
        "stopped_by_product_limit": result.stopped_by_product_limit,
        "stopped_by_source_blocked": result.stopped_by_source_blocked,
    }


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(
        marker in normalized
        for marker in SENSITIVE_FIELD_MARKERS
    )


def _sanitize_text(value: str) -> str:
    sanitized = value
    for pattern in _REDACTION_PATTERNS:
        sanitized = pattern.sub(r"\1[REDACTED]", sanitized)
    return sanitized
