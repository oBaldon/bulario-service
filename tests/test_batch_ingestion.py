from types import SimpleNamespace

import pytest

from bulario_service.anvisa import DiscoveryPage, DiscoveredProduct
from bulario_service.batch_ingestion import (
    BatchIngestionError,
    run_batch_ingestion,
)
from bulario_service.e2e_pipeline import (
    E2EPipelineError,
    ProcessedProductResult,
)
from bulario_service.models import IngestionRun


class FakeSession:
    def __init__(self):
        self._next_run_id = 1
        self.runs = {}
        self.commits = 0
        self.rollbacks = 0

    def add(self, entity):
        if isinstance(entity, IngestionRun):
            if entity.id is None:
                entity.id = self._next_run_id
                self._next_run_id += 1
            self.runs[entity.id] = entity

    def flush(self):
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def get(self, model, entity_id):
        if model is IngestionRun:
            return self.runs.get(entity_id)
        return None


def product(source_product_id: int) -> DiscoveredProduct:
    return DiscoveredProduct(
        source_product_id=source_product_id,
        registration_number=str(source_product_id),
        product_name=f"Produto {source_product_id}",
        current_expedient=None,
        company_name="Empresa",
        company_cnpj=None,
        process_number=None,
        publication_date="31/08/2026",
        raw_payload={"idProduto": source_product_id},
    )


class FakeConnector:
    def __init__(self, products):
        self.products = tuple(products)
        self.discovery_calls = []

    def discover_page(self, **kwargs):
        self.discovery_calls.append(kwargs)
        return DiscoveryPage(
            items=self.products,
            total_elements=len(self.products),
            total_pages=1,
            page=1,
            page_size=kwargs["page_size"],
            last=True,
        )


class FailingDiscoveryConnector(FakeConnector):
    def discover_page(self, **kwargs):
        raise RuntimeError("controlled discovery failure")


def install_product_processor(monkeypatch, *, failing_ids=()):
    calls = []
    failing_ids = set(failing_ids)

    def process(session, *, run, product, **kwargs):
        calls.append(product.source_product_id)
        if product.source_product_id in failing_ids:
            raise E2EPipelineError(
                f"controlled product failure {product.source_product_id}"
            )
        return ProcessedProductResult(
            item_id=product.source_product_id + 100,
            source_product_id=product.source_product_id,
            source_document_id=product.source_product_id + 1000,
            publish_action="inserted",
            public_row_id=product.source_product_id + 2000,
        )

    monkeypatch.setattr(
        "bulario_service.batch_ingestion.process_discovered_product",
        process,
    )
    return calls


def dummy_dependencies():
    return {
        "downloader": SimpleNamespace(),
        "storage": SimpleNamespace(),
        "extractor": SimpleNamespace(),
    }


def test_batch_completes_multiple_products_in_one_run(monkeypatch) -> None:
    session = FakeSession()
    connector = FakeConnector([product(10), product(20), product(30)])
    calls = install_product_processor(monkeypatch)

    result = run_batch_ingestion(
        session,
        connector=connector,
        period_start="2026-08-01T00:00:00.000Z",
        period_end="2026-08-31T23:59:59.999Z",
        page_size=25,
        **dummy_dependencies(),
    )

    assert result.run_id == 1
    assert result.run_status == "completed"
    assert result.discovered_count == 3
    assert result.processed_count == 3
    assert result.ready_count == 3
    assert result.failed_count == 0
    assert [item.source_product_id for item in result.items] == [10, 20, 30]
    assert all(item.status == "ready" for item in result.items)
    assert calls == [10, 20, 30]
    assert connector.discovery_calls == [{
        "page": 1,
        "page_size": 25,
        "period_start": "2026-08-01T00:00:00.000Z",
        "period_end": "2026-08-31T23:59:59.999Z",
    }]
    assert session.runs[1].status == "completed"


def test_product_failure_is_isolated_and_batch_continues(monkeypatch) -> None:
    session = FakeSession()
    connector = FakeConnector([product(10), product(20), product(30)])
    calls = install_product_processor(monkeypatch, failing_ids={20})

    result = run_batch_ingestion(
        session,
        connector=connector,
        period_start="2026-08-01T00:00:00.000Z",
        period_end="2026-08-31T23:59:59.999Z",
        **dummy_dependencies(),
    )

    assert calls == [10, 20, 30]
    assert result.run_status == "failed"
    assert result.ready_count == 2
    assert result.failed_count == 1
    assert [item.status for item in result.items] == [
        "ready",
        "failed",
        "ready",
    ]
    assert result.items[1].error_code == "E2EPipelineError"
    assert "controlled product failure 20" in result.items[1].error_message
    assert session.runs[1].status == "failed"


def test_empty_discovery_completes_empty_batch(monkeypatch) -> None:
    session = FakeSession()
    connector = FakeConnector([])
    calls = install_product_processor(monkeypatch)

    result = run_batch_ingestion(
        session,
        connector=connector,
        period_start="2026-08-01T00:00:00.000Z",
        period_end="2026-08-31T23:59:59.999Z",
        **dummy_dependencies(),
    )

    assert calls == []
    assert result.run_status == "completed"
    assert result.discovered_count == 0
    assert result.processed_count == 0
    assert result.ready_count == 0
    assert result.failed_count == 0
    assert result.items == ()


def test_discovery_failure_marks_run_failed(monkeypatch) -> None:
    session = FakeSession()
    connector = FailingDiscoveryConnector([])
    install_product_processor(monkeypatch)

    with pytest.raises(
        BatchIngestionError,
        match="controlled discovery failure",
    ):
        run_batch_ingestion(
            session,
            connector=connector,
            period_start="2026-08-01T00:00:00.000Z",
            period_end="2026-08-31T23:59:59.999Z",
            **dummy_dependencies(),
        )

    assert session.runs[1].status == "failed"
    assert session.rollbacks == 1


def test_invalid_page_size_is_rejected_before_run_creation() -> None:
    session = FakeSession()

    with pytest.raises(ValueError, match="page_size"):
        run_batch_ingestion(
            session,
            connector=FakeConnector([]),
            period_start="2026-08-01T00:00:00.000Z",
            period_end="2026-08-31T23:59:59.999Z",
            page_size=0,
            **dummy_dependencies(),
        )

    assert session.runs == {}
    assert session.commits == 0
