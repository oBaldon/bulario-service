from dataclasses import dataclass
import re
from typing import Literal

from bulario_service.anvisa import (
    AnvisaAccessDeniedError,
    AnvisaPayloadError,
    AnvisaPermanentSourceError,
    AnvisaTransientSourceError,
)
from bulario_service.document_storage import DocumentStorageConflictError
from bulario_service.document_text import DocumentTextExtractionError
from bulario_service.operational_persistence import (
    OperationalPersistenceConflictError,
)
from bulario_service.publication_publisher import (
    BulaPublicationConflictError,
)


FailureClass = Literal[
    "transient",
    "source_blocked",
    "permanent",
    "conflict",
    "unknown",
]


@dataclass(frozen=True)
class FailureClassification:
    error_class: FailureClass
    retryable: bool
    stop_run: bool


_TRANSIENT_HTTP_PATTERN = re.compile(
    r"HTTP\s+(500|502|503|504)\b",
    flags=re.IGNORECASE,
)
_TIMEOUT_PATTERN = re.compile(
    r"\b(timeout|timed out|connect timeout|read timeout)\b",
    flags=re.IGNORECASE,
)
_ACCESS_DENIED_PATTERN = re.compile(
    r"HTTP\s+403\b|access denied|session rejected",
    flags=re.IGNORECASE,
)


def classify_exception(error: Exception) -> FailureClassification:
    root = _root_cause(error)

    if isinstance(root, AnvisaAccessDeniedError):
        return FailureClassification(
            error_class="source_blocked",
            retryable=False,
            stop_run=True,
        )

    if isinstance(root, AnvisaTransientSourceError):
        return FailureClassification(
            error_class="transient",
            retryable=True,
            stop_run=False,
        )

    if isinstance(
        root,
        (
            DocumentStorageConflictError,
            OperationalPersistenceConflictError,
            BulaPublicationConflictError,
        ),
    ):
        return FailureClassification(
            error_class="conflict",
            retryable=False,
            stop_run=False,
        )

    if isinstance(
        root,
        (
            AnvisaPayloadError,
            AnvisaPermanentSourceError,
            DocumentTextExtractionError,
            ValueError,
        ),
    ):
        return FailureClassification(
            error_class="permanent",
            retryable=False,
            stop_run=False,
        )

    return _classify_legacy_message(str(root))


def classify_persisted_failure(
    *,
    error_class: str | None,
    error_code: str | None,
    error_message: str | None,
) -> FailureClassification:
    if error_class == "transient":
        return FailureClassification("transient", True, False)
    if error_class == "source_blocked":
        return FailureClassification("source_blocked", False, True)
    if error_class == "permanent":
        return FailureClassification("permanent", False, False)
    if error_class == "conflict":
        return FailureClassification("conflict", False, False)

    combined = " ".join(
        value
        for value in (error_code, error_message)
        if value
    )
    return _classify_legacy_message(combined)


def _root_cause(error: Exception) -> Exception:
    current = error
    seen: set[int] = set()
    while (
        current.__cause__ is not None
        and id(current) not in seen
    ):
        seen.add(id(current))
        current = current.__cause__
    return current


def _classify_legacy_message(message: str) -> FailureClassification:
    if _ACCESS_DENIED_PATTERN.search(message):
        return FailureClassification("source_blocked", False, True)
    if (
        _TRANSIENT_HTTP_PATTERN.search(message)
        or _TIMEOUT_PATTERN.search(message)
    ):
        return FailureClassification("transient", True, False)
    return FailureClassification("unknown", False, False)
