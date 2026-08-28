from pathlib import Path
from unittest.mock import Mock

from bulario_service import smoke_anvisa_session
from bulario_service.anvisa import (
    BulaVersion,
    DiscoveredProduct,
    DiscoveryPage,
    ProductDetail,
)
from bulario_service.anvisa_session import BrowserSessionState


def test_session_smoke_runs_discovery_and_detail_after_bootstrap(
    monkeypatch,
    capsys,
) -> None:
    bootstrap = Mock()
    bootstrap.bootstrap.return_value = BrowserSessionState(
        cookies=(),
        user_agent="Chrome UA",
        referer="https://consultas.anvisa.gov.br/",
    )
    monkeypatch.setattr(
        smoke_anvisa_session,
        "AnvisaBrowserSessionBootstrap",
        lambda **kwargs: bootstrap,
    )

    fake_http_client = Mock()
    fake_http_client.client = Mock()
    fake_http_client.__enter__ = Mock(return_value=fake_http_client)
    fake_http_client.__exit__ = Mock(return_value=None)
    monkeypatch.setattr(
        smoke_anvisa_session,
        "AnvisaAuthenticatedHttpClient",
        lambda state: fake_http_client,
    )

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
    monkeypatch.setattr(
        smoke_anvisa_session,
        "AnvisaBularioConnector",
        lambda client, **kwargs: connector,
    )

    result = smoke_anvisa_session.run_smoke(
        period_start="2026-08-26",
        period_end="2026-08-29",
        profile_dir=Path(".playwright/test"),
        headed=True,
        page_size=1,
    )

    assert result == 0
    bootstrap.bootstrap.assert_called_once()
    connector.discover_page.assert_called_once()
    connector.get_product_detail.assert_called_once_with(1258261)

    output = capsys.readouterr().out
    assert "Browser session bootstrap: OK" in output
    assert "Browser has been closed" in output
    assert "httpx discovery after browser close: OK" in output
    assert "httpx product detail after browser close: OK" in output


def test_session_smoke_handles_empty_discovery(
    monkeypatch,
    capsys,
) -> None:
    bootstrap = Mock()
    bootstrap.bootstrap.return_value = BrowserSessionState(
        cookies=(),
        user_agent="Chrome UA",
        referer="https://consultas.anvisa.gov.br/",
    )
    monkeypatch.setattr(
        smoke_anvisa_session,
        "AnvisaBrowserSessionBootstrap",
        lambda **kwargs: bootstrap,
    )

    fake_http_client = Mock()
    fake_http_client.client = Mock()
    fake_http_client.__enter__ = Mock(return_value=fake_http_client)
    fake_http_client.__exit__ = Mock(return_value=None)
    monkeypatch.setattr(
        smoke_anvisa_session,
        "AnvisaAuthenticatedHttpClient",
        lambda state: fake_http_client,
    )

    connector = Mock()
    connector.discover_page.return_value = DiscoveryPage(
        items=(),
        total_elements=0,
        total_pages=0,
        page=1,
        page_size=1,
        last=True,
    )
    monkeypatch.setattr(
        smoke_anvisa_session,
        "AnvisaBularioConnector",
        lambda client, **kwargs: connector,
    )

    result = smoke_anvisa_session.run_smoke(
        period_start="2026-08-26",
        period_end="2026-08-29",
        profile_dir=Path(".playwright/test"),
        headed=True,
        page_size=1,
    )

    assert result == 0
    connector.get_product_detail.assert_not_called()
    assert "Nenhum produto encontrado" in capsys.readouterr().out
