from pathlib import Path
from unittest.mock import Mock

from bulario_service import smoke_anvisa_documents
from bulario_service.anvisa import (
    BulaVersion,
    DiscoveredProduct,
    DiscoveryPage,
    ProductDetail,
)
from bulario_service.anvisa_documents import DownloadedBulaDocument
from bulario_service.anvisa_session import BrowserSessionState


def test_document_smoke_downloads_patient_and_professional(
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
        smoke_anvisa_documents,
        "AnvisaBrowserSessionBootstrap",
        lambda **kwargs: bootstrap,
    )

    fake_http = Mock()
    fake_http.client = Mock()
    fake_http.__enter__ = Mock(return_value=fake_http)
    fake_http.__exit__ = Mock(return_value=None)
    monkeypatch.setattr(
        smoke_anvisa_documents,
        "AnvisaAuthenticatedHttpClient",
        lambda state: fake_http,
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
        smoke_anvisa_documents,
        "AnvisaBularioConnector",
        lambda client, **kwargs: connector,
    )

    downloader = Mock()
    downloader.download.side_effect = [
        DownloadedBulaDocument(
            source_document_id=32630800,
            kind="patient",
            content=b"%PDF-patient",
            size_bytes=12,
            sha256="a" * 64,
            content_type="application/pdf",
        ),
        DownloadedBulaDocument(
            source_document_id=32630800,
            kind="professional",
            content=b"%PDF-professional",
            size_bytes=17,
            sha256="b" * 64,
            content_type="application/pdf",
        ),
    ]
    monkeypatch.setattr(
        smoke_anvisa_documents,
        "AnvisaDocumentDownloader",
        lambda client, **kwargs: downloader,
    )

    result = smoke_anvisa_documents.run_smoke(
        period_start="2026-08-26",
        period_end="2026-08-29",
        profile_dir=Path(".playwright/test"),
        headed=True,
        page_size=1,
    )

    assert result == 0
    assert downloader.download.call_count == 2
    assert downloader.download.call_args_list[0].kwargs == {
        "source_document_id": 32630800,
        "kind": "patient",
        "token": "patient-token",
    }
    assert downloader.download.call_args_list[1].kwargs == {
        "source_document_id": 32630800,
        "kind": "professional",
        "token": "professional-token",
    }

    output = capsys.readouterr().out
    assert "validated_pdfs=2" in output
    assert "sha256=" + ("a" * 64) in output
    assert "patient-token" not in output
    assert "professional-token" not in output


def test_document_smoke_skips_missing_tokens(
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
        smoke_anvisa_documents,
        "AnvisaBrowserSessionBootstrap",
        lambda **kwargs: bootstrap,
    )

    fake_http = Mock()
    fake_http.client = Mock()
    fake_http.__enter__ = Mock(return_value=fake_http)
    fake_http.__exit__ = Mock(return_value=None)
    monkeypatch.setattr(
        smoke_anvisa_documents,
        "AnvisaAuthenticatedHttpClient",
        lambda state: fake_http,
    )

    connector = Mock()
    connector.discover_page.return_value = DiscoveryPage(
        items=(
            DiscoveredProduct(
                source_product_id=1,
                registration_number="1",
                product_name="TESTE",
                current_expedient=None,
                company_name=None,
                company_cnpj=None,
                process_number=None,
                publication_date=None,
                raw_payload={},
            ),
        ),
        total_elements=1,
        total_pages=1,
        page=1,
        page_size=1,
        last=True,
    )
    connector.get_product_detail.return_value = ProductDetail(
        source_product_id=1,
        registration_number="1",
        product_name="TESTE",
        versions=(
            BulaVersion(
                source_document_id=10,
                expedient=None,
                registration_number="1",
                publication_date=None,
                status=None,
                patient_token=None,
                professional_token=None,
                current=True,
                raw_payload={},
            ),
        ),
    )
    monkeypatch.setattr(
        smoke_anvisa_documents,
        "AnvisaBularioConnector",
        lambda client, **kwargs: connector,
    )

    downloader = Mock()
    monkeypatch.setattr(
        smoke_anvisa_documents,
        "AnvisaDocumentDownloader",
        lambda client, **kwargs: downloader,
    )

    result = smoke_anvisa_documents.run_smoke(
        period_start="2026-08-26",
        period_end="2026-08-29",
        profile_dir=Path(".playwright/test"),
        headed=True,
        page_size=1,
    )

    assert result == 0
    downloader.download.assert_not_called()
    output = capsys.readouterr().out
    assert "no patient PDF token" in output
    assert "no professional PDF token" in output
    assert "Nenhum PDF disponível" in output
