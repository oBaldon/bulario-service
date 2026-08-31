import json
from pathlib import Path

import httpx
import pytest

from bulario_service.anvisa import (
    AnvisaAccessDeniedError,
    AnvisaBularioConnector,
    AnvisaPayloadError,
    AnvisaSourceError,
    AnvisaTransientSourceError,
    RequestTrace,
    DEFAULT_DISCOVERY_PAGE_SIZE,
)


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_discover_page_parses_observed_anvisa_payload() -> None:
    payload = load_fixture("anvisa_bulario_discovery_page.json")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://consultas.anvisa.gov.br",
    )
    connector = AnvisaBularioConnector(client=client)

    result = connector.discover_page(
        page=1,
        period_start="1000-01-01T03:06:28.000Z",
        period_end="2030-01-01T03:00:00.000Z",
    )

    assert result.total_elements == 2
    assert result.total_pages == 1
    assert result.page == 1
    assert result.page_size == 100
    assert result.last is True
    assert len(result.items) == 2

    first = result.items[0]
    assert first.source_product_id == payload["content"][0]["idProduto"]
    assert first.registration_number == payload["content"][0]["numeroRegistro"]
    assert first.product_name == payload["content"][0]["nomeProduto"]
    assert first.raw_payload["idBulaPacienteProtegido"].startswith("sanitized-")

    request = requests[0]
    assert request.headers["Authorization"] == "Guest"
    assert request.url.params["count"] == str(DEFAULT_DISCOVERY_PAGE_SIZE)
    assert request.url.params["page"] == "1"
    assert request.url.params["filter[periodoPublicacaoInicial]"] == (
        "1000-01-01T03:06:28.000Z"
    )
    assert request.url.params["filter[periodoPublicacaoFinal]"] == (
        "2030-01-01T03:00:00.000Z"
    )


def test_discover_page_converts_zero_based_response_page_to_one_based() -> None:
    payload = load_fixture("anvisa_bulario_discovery_page.json")
    payload["number"] = 4
    payload["last"] = False

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload)
        ),
        base_url="https://consultas.anvisa.gov.br",
    )

    result = AnvisaBularioConnector(client=client).discover_page(
        page=5,
        period_start="2026-08-01",
        period_end="2026-08-28",
    )

    assert result.page == 5


def test_get_product_detail_parses_history_and_deduplicates_current_version() -> None:
    payload = load_fixture("anvisa_bulario_product_detail.json")

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload)
        ),
        base_url="https://consultas.anvisa.gov.br",
    )

    result = AnvisaBularioConnector(client=client).get_product_detail(1258261)

    assert result.source_product_id == 1258261
    assert result.registration_number == "100410165"
    assert result.product_name == "ADDAVEN"
    assert len(result.versions) == 4

    current_id = payload["bulaAtual"]["idDocumento"]
    current_versions = [version for version in result.versions if version.current]
    assert len(current_versions) == 1
    assert current_versions[0].source_document_id == current_id

    ids = [version.source_document_id for version in result.versions]
    assert len(ids) == len(set(ids))
    assert current_versions[0].patient_token is not None
    assert current_versions[0].professional_token is not None


def test_get_product_detail_follows_history_pagination() -> None:
    fixture = load_fixture("anvisa_bulario_product_detail.json")
    all_versions = fixture["historico"]["content"]

    page_1 = json.loads(json.dumps(fixture))
    page_1["historico"]["content"] = all_versions[:2]
    page_1["historico"]["totalPages"] = 2
    page_1["historico"]["totalElements"] = len(all_versions)
    page_1["historico"]["last"] = False
    page_1["historico"]["number"] = 0

    page_2 = json.loads(json.dumps(fixture))
    page_2["historico"]["content"] = all_versions[2:]
    page_2["historico"]["totalPages"] = 2
    page_2["historico"]["totalElements"] = len(all_versions)
    page_2["historico"]["last"] = True
    page_2["historico"]["number"] = 1

    requested_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_pages.append(request.url.params["page"])
        payload = page_1 if request.url.params["page"] == "1" else page_2
        return httpx.Response(200, json=payload)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://consultas.anvisa.gov.br",
    )

    result = AnvisaBularioConnector(client=client).get_product_detail(1258261)

    assert requested_pages == ["1", "2"]
    assert len(result.versions) == len(all_versions)


def test_source_http_500_is_normalized() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500, json={"error": "internal"})
        ),
        base_url="https://consultas.anvisa.gov.br",
    )

    with pytest.raises(AnvisaTransientSourceError, match="HTTP 500"):
        AnvisaBularioConnector(
            client=client,
            max_attempts=1,
        ).discover_page(
            page=1,
            period_start="2026-08-01",
            period_end="2026-08-28",
        )


def test_source_timeout_is_normalized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://consultas.anvisa.gov.br",
    )

    with pytest.raises(AnvisaSourceError, match="timed out"):
        AnvisaBularioConnector(
            client=client,
            max_attempts=1,
        ).discover_page(
            page=1,
            period_start="2026-08-01",
            period_end="2026-08-28",
        )


def test_invalid_json_is_rejected() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"not-json",
                headers={"content-type": "application/json"},
            )
        ),
        base_url="https://consultas.anvisa.gov.br",
    )

    with pytest.raises(AnvisaPayloadError, match="invalid JSON"):
        AnvisaBularioConnector(
            client=client,
            max_attempts=1,
        ).discover_page(
            page=1,
            period_start="2026-08-01",
            period_end="2026-08-28",
        )


def test_unexpected_discovery_payload_is_rejected() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"unexpected": []})
        ),
        base_url="https://consultas.anvisa.gov.br",
    )

    with pytest.raises(AnvisaPayloadError, match="content"):
        AnvisaBularioConnector(
            client=client,
            max_attempts=1,
        ).discover_page(
            page=1,
            period_start="2026-08-01",
            period_end="2026-08-28",
        )


@pytest.mark.parametrize(
    ("page", "page_size"),
    [(0, 100), (-1, 100), (1, 0)],
)
def test_discover_page_rejects_invalid_pagination(page: int, page_size: int) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("request should not be sent")
        ),
        base_url="https://consultas.anvisa.gov.br",
    )

    with pytest.raises(ValueError):
        AnvisaBularioConnector(
            client=client,
            max_attempts=1,
        ).discover_page(
            page=page,
            page_size=page_size,
            period_start="2026-08-01",
            period_end="2026-08-28",
        )


def test_http_403_is_access_denied_and_not_retried() -> None:
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
        AnvisaBularioConnector(
            client=client,
            max_attempts=3,
            retry_backoff_seconds=(0, 0),
        ).discover_page(
            page=1,
            period_start="2026-08-01",
            period_end="2026-08-28",
        )

    assert calls == 1


def test_transient_500_is_retried_until_success() -> None:
    payload = load_fixture("anvisa_bulario_discovery_page.json")
    calls = 0
    traces: list[RequestTrace] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(500)
        return httpx.Response(200, json=payload)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://consultas.anvisa.gov.br",
    )

    result = AnvisaBularioConnector(
        client=client,
        max_attempts=3,
        retry_backoff_seconds=(0, 0),
        trace_sink=traces.append,
    ).discover_page(
        page=1,
        period_start="2026-08-01",
        period_end="2026-08-28",
    )

    assert result.total_elements == 2
    assert calls == 3
    assert [trace.outcome for trace in traces] == [
        "transient_http_error",
        "transient_http_error",
        "ok",
    ]
    assert all(trace.page == 1 for trace in traces)


def test_read_timeout_is_retried_and_identifies_page() -> None:
    calls = 0
    traces: list[RequestTrace] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow", request=request)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://consultas.anvisa.gov.br",
    )

    with pytest.raises(AnvisaSourceError, match=r"read timed out.*page=1"):
        AnvisaBularioConnector(
            client=client,
            max_attempts=2,
            retry_backoff_seconds=(0,),
            trace_sink=traces.append,
        ).discover_page(
            page=1,
            period_start="2026-08-01",
            period_end="2026-08-28",
        )

    assert calls == 2
    assert [trace.outcome for trace in traces] == [
        "read_timeout",
        "read_timeout",
    ]


def test_detail_trace_identifies_history_page() -> None:
    fixture = load_fixture("anvisa_bulario_product_detail.json")
    traces: list[RequestTrace] = []

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=fixture)
        ),
        base_url="https://consultas.anvisa.gov.br",
    )

    AnvisaBularioConnector(
        client=client,
        max_attempts=1,
        trace_sink=traces.append,
    ).get_product_detail(1258261)

    assert len(traces) == 1
    assert traces[0].path == "/api/consulta/bulario/1258261"
    assert traces[0].page == 1
    assert traces[0].status_code == 200
    assert traces[0].outcome == "ok"
