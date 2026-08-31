from bulario_service.anvisa import (
    AnvisaAccessDeniedError,
    AnvisaPayloadError,
    AnvisaPermanentSourceError,
    AnvisaTransientSourceError,
)
from bulario_service.document_storage import DocumentStorageConflictError
from bulario_service.retry_policy import (
    classify_exception,
    classify_persisted_failure,
)


def test_transient_source_error_is_retryable() -> None:
    result = classify_exception(
        AnvisaTransientSourceError("ANVISA returned HTTP 500")
    )

    assert result.error_class == "transient"
    assert result.retryable is True
    assert result.stop_run is False


def test_access_denied_stops_run_without_blind_retry() -> None:
    result = classify_exception(
        AnvisaAccessDeniedError("ANVISA returned HTTP 403")
    )

    assert result.error_class == "source_blocked"
    assert result.retryable is False
    assert result.stop_run is True


def test_invalid_payload_is_permanent() -> None:
    result = classify_exception(
        AnvisaPayloadError("invalid payload")
    )

    assert result.error_class == "permanent"
    assert result.retryable is False


def test_non_retryable_source_http_is_permanent() -> None:
    result = classify_exception(
        AnvisaPermanentSourceError("ANVISA returned HTTP 404")
    )

    assert result.error_class == "permanent"
    assert result.retryable is False


def test_storage_conflict_is_conflict() -> None:
    result = classify_exception(
        DocumentStorageConflictError("different bytes")
    )

    assert result.error_class == "conflict"
    assert result.retryable is False


def test_wrapped_transient_error_uses_root_cause() -> None:
    try:
        try:
            raise AnvisaTransientSourceError(
                "ANVISA returned HTTP 503"
            )
        except AnvisaTransientSourceError as exc:
            raise RuntimeError("pipeline wrapper") from exc
    except RuntimeError as wrapped:
        result = classify_exception(wrapped)

    assert result.error_class == "transient"
    assert result.retryable is True


def test_legacy_http_500_failure_is_still_retryable() -> None:
    result = classify_persisted_failure(
        error_class=None,
        error_code="AnvisaSourceError",
        error_message=(
            "ANVISA returned HTTP 500 "
            "path=/api/consulta/bulario/4729 page=2"
        ),
    )

    assert result.error_class == "transient"
    assert result.retryable is True


def test_legacy_http_403_failure_is_source_blocked() -> None:
    result = classify_persisted_failure(
        error_class=None,
        error_code="AnvisaSourceError",
        error_message="ANVISA returned HTTP 403 path=/api/consulta/bulario",
    )

    assert result.error_class == "source_blocked"
    assert result.stop_run is True
