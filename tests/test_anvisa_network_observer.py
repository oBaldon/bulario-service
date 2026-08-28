from pathlib import Path

import pytest

from bulario_service.anvisa_network_observer import (
    ObservedRequest,
    _is_target,
    _redact_headers,
    build_parser,
)


def test_target_detection_accepts_bulario_list_and_detail() -> None:
    assert _is_target(
        "https://consultas.anvisa.gov.br/api/consulta/bulario?count=1&page=1"
    )
    assert _is_target(
        "https://consultas.anvisa.gov.br/api/consulta/bulario/1258261?count=10&page=1"
    )


def test_target_detection_accepts_pdf_endpoint() -> None:
    assert _is_target(
        "https://consultas.anvisa.gov.br/"
        "api/consulta/medicamentos/arquivo/bula/parecer/token/"
    )


def test_target_detection_rejects_unrelated_assets() -> None:
    assert not _is_target(
        "https://consultas.anvisa.gov.br/assets/app.js"
    )


def test_sensitive_headers_are_redacted() -> None:
    headers = {
        "accept": "application/json",
        "authorization": "Guest",
        "cookie": "secret=value",
        "user-agent": "Browser UA",
    }

    redacted = _redact_headers(headers)

    assert redacted["accept"] == "application/json"
    assert redacted["authorization"] == "<redacted>"
    assert redacted["cookie"] == "<redacted>"
    assert redacted["user-agent"] == "Browser UA"


def test_observed_request_keeps_only_safe_representation() -> None:
    observed = ObservedRequest(
        method="GET",
        url="https://consultas.anvisa.gov.br/api/consulta/bulario?count=1",
        path="/api/consulta/bulario",
        status_code=200,
        request_headers={"authorization": "<redacted>"},
        response_headers={"content-type": "application/json"},
    )

    assert observed.status_code == 200
    assert observed.path == "/api/consulta/bulario"
    assert observed.request_headers["authorization"] == "<redacted>"


def test_parser_defaults_to_controlled_timeout_and_profile() -> None:
    args = build_parser().parse_args([])

    assert args.profile_dir == Path(".playwright/anvisa-profile-google-chrome")
    assert args.browser_channel == "chrome"
    assert args.timeout_seconds == 120.0
    assert args.headed is False


def test_parser_accepts_headed_and_custom_timeout() -> None:
    args = build_parser().parse_args(
        ["--headed", "--timeout-seconds", "30"]
    )

    assert args.headed is True
    assert args.timeout_seconds == 30.0


def test_timeout_must_be_numeric() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--timeout-seconds", "abc"])


def test_network_observer_defaults_to_google_chrome_channel() -> None:
    args = build_parser().parse_args([])
    assert args.browser_channel == "chrome"
