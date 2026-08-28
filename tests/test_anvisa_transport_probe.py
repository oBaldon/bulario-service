from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from bulario_service.anvisa_transport_probe import (
    ProbeResult,
    browser_cookies_to_httpx,
    build_discovery_params,
    build_parser,
    probe_httpx_from_browser_session,
)


def test_build_discovery_params_uses_controlled_single_page() -> None:
    params = build_discovery_params(
        period_start="2026-08-26T00:00:00.000Z",
        period_end="2026-08-29T00:00:00.000Z",
    )

    assert params["count"] == 1
    assert params["page"] == 1
    assert params["filter[periodoPublicacaoInicial]"] == (
        "2026-08-26T00:00:00.000Z"
    )
    assert params["filter[periodoPublicacaoFinal]"] == (
        "2026-08-29T00:00:00.000Z"
    )


def test_build_discovery_params_rejects_invalid_page_size() -> None:
    with pytest.raises(ValueError):
        build_discovery_params(
            period_start="2026-08-26",
            period_end="2026-08-29",
            page_size=0,
        )


def test_browser_cookies_are_copied_to_httpx_jar() -> None:
    jar = browser_cookies_to_httpx(
        [
            {
                "name": "session-cookie",
                "value": "session-value",
                "domain": ".anvisa.gov.br",
                "path": "/",
            },
            {
                "name": "ignored",
                "value": 123,
                "domain": ".anvisa.gov.br",
                "path": "/",
            },
        ]
    )

    request = httpx.Request(
        "GET",
        "https://consultas.anvisa.gov.br/api/consulta/bulario",
    )
    jar.set_cookie_header(request)

    cookie_header = request.headers["Cookie"]
    assert "session-cookie=session-value" in cookie_header
    assert "ignored=" not in cookie_header


def test_parser_defaults_to_headless_and_dedicated_profile() -> None:
    args = build_parser().parse_args(
        [
            "--period-start",
            "2026-08-26",
            "--period-end",
            "2026-08-29",
        ]
    )

    assert args.headed is False
    assert args.headless is False
    assert args.profile_dir == Path(".playwright/anvisa-profile-google-chrome")
    assert args.browser_channel == "chrome"
    assert args.page_size == 1


def test_parser_accepts_headed_mode() -> None:
    args = build_parser().parse_args(
        [
            "--period-start",
            "2026-08-26",
            "--period-end",
            "2026-08-29",
            "--headed",
        ]
    )

    assert args.headed is True


def test_probe_result_is_explicit_about_transport_status() -> None:
    result = ProbeResult(
        transport="context.request",
        status_code=200,
        ok=True,
        detail="totalElements=8831 numberOfElements=1",
    )

    assert result.transport == "context.request"
    assert result.status_code == 200
    assert result.ok is True


def test_httpx_probe_uses_browser_cookies_and_user_agent(monkeypatch) -> None:
    context = Mock()
    context.cookies.return_value = [
        {
            "name": "session-cookie",
            "value": "session-value",
            "domain": ".anvisa.gov.br",
            "path": "/",
        }
    ]
    page = Mock()
    page.evaluate.return_value = "Browser UA"

    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "totalElements": 8831,
                "numberOfElements": 1,
            }

    class FakeClient:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def get(self, path, *, params):
            captured["path"] = path
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr(
        "bulario_service.anvisa_transport_probe.httpx.Client",
        FakeClient,
    )

    result = probe_httpx_from_browser_session(
        context,
        page,
        params={"count": 1},
    )

    assert result.ok is True
    assert result.status_code == 200
    assert captured["path"] == "/api/consulta/bulario"
    assert captured["kwargs"]["headers"]["User-Agent"] == "Browser UA"
    assert captured["kwargs"]["headers"]["Authorization"] == "Guest"


def test_transport_probe_defaults_to_google_chrome_channel() -> None:
    args = build_parser().parse_args(
        [
            "--period-start",
            "2026-08-26",
            "--period-end",
            "2026-08-29",
        ]
    )
    assert args.browser_channel == "chrome"
