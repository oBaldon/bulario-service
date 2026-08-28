from pathlib import Path
from unittest.mock import Mock

from bulario_service import smoke_anvisa_storage
from bulario_service.anvisa import (
    BulaVersion,
    DiscoveredProduct,
    DiscoveryPage,
    ProductDetail,
)
from bulario_service.anvisa_documents import DownloadedBulaDocument
from bulario_service.anvisa_session import BrowserSessionState
from bulario_service.document_storage import StoredBulaDocument


def test_storage_smoke_stores_both_current_pdfs(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    bootstrap = Mock()
    bootstrap.bootstrap.return_value = BrowserSessionState(
        cookies=(),
        user_agent="Chrome UA",
        referer="https://consultas.anvisa.gov.br/",
    )
    monkeypatch.setattr(
        smoke_anvisa_storage,
        "AnvisaBrowserSessionBootstrap",
        lambda **kwargs: bootstrap,
    )

    fake_http = Mock()
    fake_http.client = Mock()
    fake_http.__enter__ = Mock(return_value=fake_http)
    fake_http.__exit__ = Mock(return_value=None)
    monkeypatch.setattr(
        smoke_anvisa_storage,
        "AnvisaAuthenticatedHttpClient",
        lambda state: fake_http,
    )

    product = DiscoveredProduct(
        source_product_id=1174609,
        registration_number="123",
        product_name="TESTE",
        current_expedient="456",
        company_name="EMPRESA",
        company_cnpj="00000000000000",
        process_number="25351000000000000",
        publication_date="2026-08-28",
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
        source_document_id=35480554,
        expedient="456",
        registration_number="123",
        publication_date="28/08/2026",
        status=None,
        patient_token="patient-token",
        professional_token="professional-token",
        current=True,
        raw_payload={},
    )
    detail = ProductDetail(
        source_product_id=1174609,
        registration_number="123",
        product_name="TESTE",
        versions=(version,),
    )

    connector = Mock()
    connector.discover_page.return_value = discovery
    connector.get_product_detail.return_value = detail
    monkeypatch.setattr(
        smoke_anvisa_storage,
        "AnvisaBularioConnector",
        lambda client, **kwargs: connector,
    )

    downloader = Mock()
    downloader.download.side_effect = [
        DownloadedBulaDocument(
            source_document_id=35480554,
            kind="patient",
            content=b"%PDF-patient",
            size_bytes=12,
            sha256="a" * 64,
            content_type="application/pdf",
        ),
        DownloadedBulaDocument(
            source_document_id=35480554,
            kind="professional",
            content=b"%PDF-professional",
            size_bytes=17,
            sha256="b" * 64,
            content_type="application/pdf",
        ),
    ]
    monkeypatch.setattr(
        smoke_anvisa_storage,
        "AnvisaDocumentDownloader",
        lambda client, **kwargs: downloader,
    )

    storage = Mock()
    storage.root = tmp_path.resolve()
    storage.store.side_effect = [
        StoredBulaDocument(
            source_product_id=1174609,
            source_document_id=35480554,
            kind="patient",
            storage_key="bulas/1174609/35480554/patient.pdf",
            sha256="a" * 64,
            size_bytes=12,
        ),
        StoredBulaDocument(
            source_product_id=1174609,
            source_document_id=35480554,
            kind="professional",
            storage_key=(
                "bulas/1174609/35480554/professional.pdf"
            ),
            sha256="b" * 64,
            size_bytes=17,
        ),
    ]
    monkeypatch.setattr(
        smoke_anvisa_storage,
        "LocalDocumentStorage",
        lambda root: storage,
    )

    result = smoke_anvisa_storage.run_smoke(
        period_start="2026-08-26",
        period_end="2026-08-29",
        profile_dir=Path(".playwright/test"),
        storage_root=tmp_path,
        headed=True,
        page_size=1,
    )

    assert result == 0
    assert storage.store.call_count == 2

    output = capsys.readouterr().out
    assert "stored_pdfs=2" in output
    assert "bulas/1174609/35480554/patient.pdf" in output
    assert "patient-token" not in output
    assert "professional-token" not in output
