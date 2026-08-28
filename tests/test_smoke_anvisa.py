from unittest.mock import Mock

import pytest

from bulario_service import smoke_anvisa
from bulario_service.anvisa import (
    AnvisaSourceError,
    BulaVersion,
    DiscoveredProduct,
    DiscoveryPage,
    ProductDetail,
)


def test_smoke_calls_discovery_and_detail(monkeypatch, capsys) -> None:
    product = DiscoveredProduct(
        source_product_id=1258261,
        registration_number="100410165",
        product_name="ADDAVEN",
        current_expedient="1343401241",
        company_name="EMPRESA",
        company_cnpj="00000000000000",
        process_number="25351000000000000",
        publication_date="2024-09-30",
        raw_payload={},
    )
    discovery = DiscoveryPage(
        items=(product,),
        total_elements=1,
        total_pages=1,
        page=1,
        page_size=1,
        last=True,
    )
    version = BulaVersion(
        source_document_id=32630800,
        expedient="1343401241",
        registration_number="100410165",
        publication_date="30/09/2024",
        status="Aditado ao processo",
        patient_token="patient-token",
        professional_token="professional-token",
        current=True,
        raw_payload={},
    )
    detail = ProductDetail(
        source_product_id=1258261,
        registration_number="100410165",
        product_name="ADDAVEN",
        versions=(version,),
    )

    connector = Mock()
    connector.discover_page.return_value = discovery
    connector.get_product_detail.return_value = detail
    connector.__enter__ = Mock(return_value=connector)
    connector.__exit__ = Mock(return_value=None)

    monkeypatch.setattr(smoke_anvisa, "AnvisaBularioConnector", lambda: connector)

    result = smoke_anvisa.run_smoke(
        period_start="2026-08-26T00:00:00.000Z",
        period_end="2026-08-29T00:00:00.000Z",
        page_size=1,
    )

    assert result == 0
    connector.discover_page.assert_called_once_with(
        page=1,
        page_size=1,
        period_start="2026-08-26T00:00:00.000Z",
        period_end="2026-08-29T00:00:00.000Z",
    )
    connector.get_product_detail.assert_called_once_with(1258261)

    output = capsys.readouterr().out
    assert "ANVISA discovery: OK" in output
    assert "ANVISA product detail: OK" in output
    assert "current_source_document_id=32630800" in output


def test_smoke_stops_cleanly_when_period_has_no_results(monkeypatch, capsys) -> None:
    discovery = DiscoveryPage(
        items=(),
        total_elements=0,
        total_pages=0,
        page=1,
        page_size=1,
        last=True,
    )

    connector = Mock()
    connector.discover_page.return_value = discovery
    connector.__enter__ = Mock(return_value=connector)
    connector.__exit__ = Mock(return_value=None)

    monkeypatch.setattr(smoke_anvisa, "AnvisaBularioConnector", lambda: connector)

    result = smoke_anvisa.run_smoke(
        period_start="2026-08-26",
        period_end="2026-08-29",
        page_size=1,
    )

    assert result == 0
    connector.get_product_detail.assert_not_called()
    assert "Nenhum produto encontrado" in capsys.readouterr().out


def test_smoke_returns_diagnostic_exit_code_on_source_failure(monkeypatch, capsys) -> None:
    connector = Mock()
    connector.discover_page.side_effect = AnvisaSourceError(
        "ANVISA returned HTTP 403"
    )
    connector.__enter__ = Mock(return_value=connector)
    connector.__exit__ = Mock(return_value=None)

    monkeypatch.setattr(smoke_anvisa, "AnvisaBularioConnector", lambda: connector)

    result = smoke_anvisa.run_smoke(
        period_start="2026-08-26",
        period_end="2026-08-29",
        page_size=1,
    )

    assert result == 2
    error = capsys.readouterr().err
    assert "ANVISA smoke test failed" in error
    assert "HTTP 403" in error


def test_parser_defaults_to_single_record_request() -> None:
    args = smoke_anvisa.build_parser().parse_args([])

    assert args.page_size == 1
    assert args.period_start.endswith("T00:00:00.000Z")
    assert args.period_end.endswith("T00:00:00.000Z")


def test_parser_rejects_non_integer_page_size() -> None:
    with pytest.raises(SystemExit):
        smoke_anvisa.build_parser().parse_args(["--page-size", "x"])
