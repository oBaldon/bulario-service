import hashlib

import httpx
import pytest

from bulario_service.anvisa import (
    AnvisaAccessDeniedError,
    AnvisaPermanentSourceError,
    AnvisaSourceError,
    AnvisaTransientSourceError,
)
from bulario_service.anvisa_documents import (
    AnvisaDocumentDownloader,
    DocumentDownloadTrace,
)


PDF_BYTES = b"%PDF-1.7\nminimal test pdf\n%%EOF"


def test_download_valid_pdf_returns_hash_and_metadata() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=PDF_BYTES,
            headers={"content-type": "application/force-download"},
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://consultas.anvisa.gov.br",
    )

    result = AnvisaDocumentDownloader(
        client,
        max_attempts=1,
    ).download(
        source_document_id=32630800,
        kind="patient",
        token="temporary-token",
    )

    assert result.source_document_id == 32630800
    assert result.kind == "patient"
    assert result.content == PDF_BYTES
    assert result.size_bytes == len(PDF_BYTES)
    assert result.sha256 == hashlib.sha256(PDF_BYTES).hexdigest()
    assert result.content_type == "application/force-download"

    request = requests[0]
    assert request.url.path.endswith("/parecer/temporary-token/")
    assert request.url.params["Authorization"] == ""


def test_download_rejects_empty_content() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"")
        ),
        base_url="https://consultas.anvisa.gov.br",
    )

    with pytest.raises(AnvisaPermanentSourceError, match="empty document"):
        AnvisaDocumentDownloader(
            client,
            max_attempts=1,
        ).download(
            source_document_id=1,
            kind="patient",
            token="token",
        )


def test_download_rejects_non_pdf_content() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"<html>blocked</html>",
            )
        ),
        base_url="https://consultas.anvisa.gov.br",
    )

    with pytest.raises(AnvisaPermanentSourceError, match="invalid PDF"):
        AnvisaDocumentDownloader(
            client,
            max_attempts=1,
        ).download(
            source_document_id=1,
            kind="professional",
            token="token",
        )


def test_document_403_is_access_denied_and_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://consultas.anvisa.gov.br",
    )

    with pytest.raises(AnvisaAccessDeniedError, match="HTTP 403"):
        AnvisaDocumentDownloader(
            client,
            max_attempts=3,
            retry_backoff_seconds=(0, 0),
        ).download(
            source_document_id=1,
            kind="patient",
            token="token",
        )

    assert calls == 1


def test_document_transient_500_is_retried() -> None:
    calls = 0
    traces: list[DocumentDownloadTrace] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500)
        return httpx.Response(200, content=PDF_BYTES)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://consultas.anvisa.gov.br",
    )

    result = AnvisaDocumentDownloader(
        client,
        max_attempts=2,
        retry_backoff_seconds=(0,),
        trace_sink=traces.append,
    ).download(
        source_document_id=123,
        kind="patient",
        token="sensitive-token",
    )

    assert result.size_bytes == len(PDF_BYTES)
    assert calls == 2
    assert [trace.outcome for trace in traces] == [
        "transient_http_error",
        "ok",
    ]


def test_document_trace_never_contains_token() -> None:
    traces: list[DocumentDownloadTrace] = []

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=PDF_BYTES)
        ),
        base_url="https://consultas.anvisa.gov.br",
    )

    AnvisaDocumentDownloader(
        client,
        max_attempts=1,
        trace_sink=traces.append,
    ).download(
        source_document_id=999,
        kind="professional",
        token="super-secret-token",
    )

    assert len(traces) == 1
    serialized = repr(traces[0])
    assert "super-secret-token" not in serialized
    assert traces[0].source_document_id == 999
    assert traces[0].kind == "professional"
    assert traces[0].status_code == 200
    assert traces[0].size_bytes == len(PDF_BYTES)


@pytest.mark.parametrize("kind", ["other", "", "PATIENT"])
def test_download_rejects_unknown_document_kind(kind: str) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("request should not be sent")
        ),
        base_url="https://consultas.anvisa.gov.br",
    )

    with pytest.raises(ValueError, match="kind"):
        AnvisaDocumentDownloader(
            client,
            max_attempts=1,
        ).download(
            source_document_id=1,
            kind=kind,  # type: ignore[arg-type]
            token="token",
        )


def test_download_rejects_empty_token_without_request() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("request should not be sent")
        ),
        base_url="https://consultas.anvisa.gov.br",
    )

    with pytest.raises(ValueError, match="token"):
        AnvisaDocumentDownloader(
            client,
            max_attempts=1,
        ).download(
            source_document_id=1,
            kind="patient",
            token="",
        )



def test_document_exhausted_500_is_transient() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500)
        ),
        base_url="https://consultas.anvisa.gov.br",
    )

    with pytest.raises(
        AnvisaTransientSourceError,
        match="HTTP 500",
    ):
        AnvisaDocumentDownloader(
            client,
            max_attempts=1,
        ).download(
            source_document_id=123,
            kind="patient",
            token="token",
        )
