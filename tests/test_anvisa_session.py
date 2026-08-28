from pathlib import Path
from unittest.mock import Mock

import httpx

from bulario_service.anvisa_session import (
    AnvisaAuthenticatedHttpClient,
    BrowserSessionState,
)


def test_authenticated_http_client_uses_browser_session_state() -> None:
    state = BrowserSessionState(
        cookies=(
            {
                "name": "session-cookie",
                "value": "session-value",
                "domain": ".anvisa.gov.br",
                "path": "/",
            },
        ),
        user_agent="Google Chrome UA",
        referer="https://consultas.anvisa.gov.br/",
    )

    client = AnvisaAuthenticatedHttpClient(state)

    try:
        request = client.client.build_request(
            "GET",
            "/api/consulta/bulario",
        )

        assert request.headers["User-Agent"] == "Google Chrome UA"
        assert request.headers["Authorization"] == "Guest"
        assert request.headers["Referer"] == (
            "https://consultas.anvisa.gov.br/"
        )
        assert "session-cookie=session-value" in request.headers["Cookie"]
    finally:
        client.close()


def test_browser_session_state_does_not_require_persistence() -> None:
    state = BrowserSessionState(
        cookies=(),
        user_agent="UA",
        referer="https://consultas.anvisa.gov.br/",
    )

    assert state.cookies == ()
    assert state.user_agent == "UA"


def test_authenticated_http_client_uses_longer_read_timeout() -> None:
    state = BrowserSessionState(
        cookies=(),
        user_agent="Chrome UA",
        referer="https://consultas.anvisa.gov.br/",
    )

    client = AnvisaAuthenticatedHttpClient(state)
    try:
        timeout = client.client.timeout
        assert timeout.read == 60.0
        assert timeout.connect == 10.0
    finally:
        client.close()
