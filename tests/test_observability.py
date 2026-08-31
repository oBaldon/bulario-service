import io
import json

from bulario_service.batch_ingestion import (
    BatchIngestionResult,
    BatchItemResult,
)
from bulario_service.observability import (
    emit_observation,
    result_metrics,
    sanitize_observation,
)


def build_result() -> BatchIngestionResult:
    return BatchIngestionResult(
        run_id=31,
        run_status="paused",
        run_mode="reconciliation",
        period_start="2026-08-01T00:00:00.000Z",
        period_end="2026-08-31T23:59:59.999Z",
        resumed=True,
        start_page=3,
        last_completed_page=4,
        pages_fetched=2,
        discovered_count=4,
        duplicate_count=1,
        skipped_terminal_count=1,
        processed_count=3,
        ready_count=2,
        failed_count=1,
        stopped_by_page_limit=False,
        stopped_by_product_limit=True,
        stopped_by_source_blocked=False,
        retry_count=2,
        source_total_elements=394,
        invocation_duration_seconds=17.141,
        items=(
            BatchItemResult(
                source_product_id=1,
                status="ready",
                item_id=1,
                source_document_id=10,
                publish_action="inserted",
                public_row_id=100,
            ),
            BatchItemResult(
                source_product_id=2,
                status="ready",
                item_id=2,
                source_document_id=20,
                publish_action="unchanged",
                public_row_id=101,
            ),
            BatchItemResult(
                source_product_id=3,
                status="failed",
                item_id=3,
                error_code="ConflictError",
                error_message="material divergence",
                error_class="conflict",
                retry_count=2,
            ),
        ),
    )


def test_result_metrics_cover_operational_run_summary() -> None:
    metrics = result_metrics(build_result())

    assert metrics == {
        "run_id": 31,
        "run_status": "paused",
        "mode": "reconciliation",
        "period_start": "2026-08-01T00:00:00.000Z",
        "period_end": "2026-08-31T23:59:59.999Z",
        "resumed": True,
        "start_page": 3,
        "checkpoint_page": 4,
        "pages_fetched": 2,
        "source_total_elements": 394,
        "discovered": 4,
        "duplicates": 1,
        "skipped_terminal": 1,
        "processed": 3,
        "ready": 2,
        "failed": 1,
        "published": 1,
        "inserted": 1,
        "unchanged": 1,
        "conflicts": 1,
        "retries": 2,
        "failed_by_class": {"conflict": 1},
        "duration_seconds": 17.141,
        "stopped_by_page_limit": False,
        "stopped_by_product_limit": True,
        "stopped_by_source_blocked": False,
    }


def test_emit_observation_writes_one_json_line() -> None:
    stream = io.StringIO()

    emit_observation(
        "sync_started",
        stream=stream,
        mode="incremental",
        resume_run_id=8,
    )

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["service"] == "bulario-service"
    assert payload["event"] == "sync_started"
    assert payload["mode"] == "incremental"
    assert payload["resume_run_id"] == 8
    assert payload["timestamp"].endswith("Z")


def test_observation_redacts_sensitive_keys_recursively() -> None:
    sanitized = sanitize_observation({
        "Authorization": "Guest secret",
        "nested": {
            "cookie_value": "session=abc",
            "safe": "ok",
        },
        "token": "temporary-token",
    })

    assert sanitized["Authorization"] == "[REDACTED]"
    assert sanitized["nested"]["cookie_value"] == "[REDACTED]"
    assert sanitized["nested"]["safe"] == "ok"
    assert sanitized["token"] == "[REDACTED]"


def test_observation_redacts_sensitive_values_inside_error_text() -> None:
    sanitized = sanitize_observation(
        "request failed Authorization=abc123 token=xyz Cookie=sid=secret"
    )

    assert "abc123" not in sanitized
    assert "xyz" not in sanitized
    assert "sid=secret" not in sanitized
    assert "[REDACTED]" in sanitized


def test_observation_preserves_non_sensitive_operational_values() -> None:
    value = (
        "ANVISA returned HTTP 500 "
        "path=/api/consulta/bulario/4729 page=2"
    )

    assert sanitize_observation(value) == value
