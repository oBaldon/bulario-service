from dataclasses import dataclass
import hashlib
import time
from typing import Callable, Literal

import httpx

from bulario_service.anvisa import (
    AnvisaAccessDeniedError,
    AnvisaPermanentSourceError,
    AnvisaSourceError,
    AnvisaTransientSourceError,
)


DocumentKind = Literal["patient", "professional"]


@dataclass(frozen=True)
class DownloadedBulaDocument:
    source_document_id: int
    kind: DocumentKind
    content: bytes
    size_bytes: int
    sha256: str
    content_type: str | None


@dataclass(frozen=True)
class DocumentDownloadTrace:
    source_document_id: int
    kind: DocumentKind
    attempt: int
    status_code: int | None
    elapsed_seconds: float
    outcome: str
    size_bytes: int | None = None


DocumentTraceSink = Callable[[DocumentDownloadTrace], None]


class AnvisaDocumentDownloader:
    def __init__(
        self,
        client: httpx.Client,
        *,
        max_attempts: int = 3,
        retry_backoff_seconds: tuple[float, ...] = (2.0, 5.0),
        trace_sink: DocumentTraceSink | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be greater than or equal to 1")
        if len(retry_backoff_seconds) < max_attempts - 1:
            raise ValueError(
                "retry_backoff_seconds must cover all retry attempts"
            )

        self._client = client
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._trace_sink = trace_sink

    def download(
        self,
        *,
        source_document_id: int,
        kind: DocumentKind,
        token: str,
    ) -> DownloadedBulaDocument:
        if source_document_id < 1:
            raise ValueError(
                "source_document_id must be greater than or equal to 1"
            )
        if kind not in {"patient", "professional"}:
            raise ValueError("kind must be patient or professional")
        if not token:
            raise ValueError("token must not be empty")

        path = (
            "/api/consulta/medicamentos/arquivo/bula/parecer/"
            f"{token}/"
        )

        for attempt in range(1, self._max_attempts + 1):
            started = time.monotonic()
            try:
                response = self._client.get(
                    path,
                    params={"Authorization": ""},
                    headers={"Accept": "application/pdf,*/*"},
                )
            except httpx.ConnectTimeout as exc:
                elapsed = time.monotonic() - started
                self._emit_trace(
                    source_document_id=source_document_id,
                    kind=kind,
                    attempt=attempt,
                    status_code=None,
                    elapsed_seconds=elapsed,
                    outcome="connect_timeout",
                )
                if attempt < self._max_attempts:
                    self._sleep_before_retry(attempt)
                    continue
                raise AnvisaTransientSourceError(
                    "ANVISA document connect timed out "
                    f"source_document_id={source_document_id} kind={kind}"
                ) from exc
            except httpx.ReadTimeout as exc:
                elapsed = time.monotonic() - started
                self._emit_trace(
                    source_document_id=source_document_id,
                    kind=kind,
                    attempt=attempt,
                    status_code=None,
                    elapsed_seconds=elapsed,
                    outcome="read_timeout",
                )
                if attempt < self._max_attempts:
                    self._sleep_before_retry(attempt)
                    continue
                raise AnvisaTransientSourceError(
                    "ANVISA document read timed out "
                    f"source_document_id={source_document_id} kind={kind}"
                ) from exc
            except httpx.TimeoutException as exc:
                elapsed = time.monotonic() - started
                self._emit_trace(
                    source_document_id=source_document_id,
                    kind=kind,
                    attempt=attempt,
                    status_code=None,
                    elapsed_seconds=elapsed,
                    outcome="timeout",
                )
                if attempt < self._max_attempts:
                    self._sleep_before_retry(attempt)
                    continue
                raise AnvisaTransientSourceError(
                    "ANVISA document request timed out "
                    f"source_document_id={source_document_id} kind={kind}"
                ) from exc
            except httpx.HTTPError as exc:
                elapsed = time.monotonic() - started
                self._emit_trace(
                    source_document_id=source_document_id,
                    kind=kind,
                    attempt=attempt,
                    status_code=None,
                    elapsed_seconds=elapsed,
                    outcome="http_error",
                )
                raise AnvisaTransientSourceError(
                    "ANVISA document request failed "
                    f"source_document_id={source_document_id} kind={kind}"
                ) from exc

            elapsed = time.monotonic() - started
            status = response.status_code

            if status == 403:
                self._emit_trace(
                    source_document_id=source_document_id,
                    kind=kind,
                    attempt=attempt,
                    status_code=status,
                    elapsed_seconds=elapsed,
                    outcome="access_denied",
                )
                raise AnvisaAccessDeniedError(
                    "ANVISA returned HTTP 403 for document "
                    f"source_document_id={source_document_id} kind={kind}"
                )

            if status in {500, 502, 503, 504}:
                self._emit_trace(
                    source_document_id=source_document_id,
                    kind=kind,
                    attempt=attempt,
                    status_code=status,
                    elapsed_seconds=elapsed,
                    outcome="transient_http_error",
                )
                if attempt < self._max_attempts:
                    self._sleep_before_retry(attempt)
                    continue

            if status != 200:
                self._emit_trace(
                    source_document_id=source_document_id,
                    kind=kind,
                    attempt=attempt,
                    status_code=status,
                    elapsed_seconds=elapsed,
                    outcome="http_error",
                )
                error_type = (
                    AnvisaTransientSourceError
                    if status in {500, 502, 503, 504}
                    else AnvisaPermanentSourceError
                )
                raise error_type(
                    f"ANVISA returned HTTP {status} for document "
                    f"source_document_id={source_document_id} kind={kind}"
                )

            content = response.content
            if not content:
                self._emit_trace(
                    source_document_id=source_document_id,
                    kind=kind,
                    attempt=attempt,
                    status_code=status,
                    elapsed_seconds=elapsed,
                    outcome="empty_document",
                    size_bytes=0,
                )
                raise AnvisaPermanentSourceError(
                    "ANVISA returned empty document "
                    f"source_document_id={source_document_id} kind={kind}"
                )

            if not content.startswith(b"%PDF-"):
                self._emit_trace(
                    source_document_id=source_document_id,
                    kind=kind,
                    attempt=attempt,
                    status_code=status,
                    elapsed_seconds=elapsed,
                    outcome="invalid_pdf",
                    size_bytes=len(content),
                )
                raise AnvisaPermanentSourceError(
                    "ANVISA returned invalid PDF content "
                    f"source_document_id={source_document_id} kind={kind}"
                )

            sha256 = hashlib.sha256(content).hexdigest()
            self._emit_trace(
                source_document_id=source_document_id,
                kind=kind,
                attempt=attempt,
                status_code=status,
                elapsed_seconds=elapsed,
                outcome="ok",
                size_bytes=len(content),
            )

            return DownloadedBulaDocument(
                source_document_id=source_document_id,
                kind=kind,
                content=content,
                size_bytes=len(content),
                sha256=sha256,
                content_type=response.headers.get("content-type"),
            )

        raise AssertionError("unreachable")

    def _sleep_before_retry(self, attempt: int) -> None:
        time.sleep(self._retry_backoff_seconds[attempt - 1])

    def _emit_trace(
        self,
        *,
        source_document_id: int,
        kind: DocumentKind,
        attempt: int,
        status_code: int | None,
        elapsed_seconds: float,
        outcome: str,
        size_bytes: int | None = None,
    ) -> None:
        if self._trace_sink is None:
            return

        self._trace_sink(
            DocumentDownloadTrace(
                source_document_id=source_document_id,
                kind=kind,
                attempt=attempt,
                status_code=status_code,
                elapsed_seconds=elapsed_seconds,
                outcome=outcome,
                size_bytes=size_bytes,
            )
        )
