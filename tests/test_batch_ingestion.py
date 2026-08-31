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
    def __init__(self, pages):
        self.pages = tuple(tuple(items) for items in pages)
        self.discovery_calls = []

    def discover_page(self, **kwargs):
        self.discovery_calls.append(kwargs)
        page = kwargs["page"]
        if page > len(self.pages):
            raise AssertionError(f"unexpected discovery page {page}")
        items = self.pages[page - 1]
        return DiscoveryPage(
            items=items,
            total_elements=sum(len(value) for value in self.pages),
            total_pages=len(self.pages),
            page=page,
            page_size=kwargs["page_size"],
            last=page == len(self.pages),
        )


class FailingPageConnector(FakeConnector):
    def __init__(self, pages, *, failing_page):
        super().__init__(pages)
        self.failing_page = failing_page

    def discover_page(self, **kwargs):
        if kwargs["page"] == self.failing_page:
            raise RuntimeError("controlled discovery failure")
        return super().discover_page(**kwargs)


class InconsistentPaginationConnector(FakeConnector):
    def discover_page(self, **kwargs):
        self.discovery_calls.append(kwargs)
        return DiscoveryPage(
            items=(product(10),),
            total_elements=1,
            total_pages=1,
            page=1,
            page_size=kwargs["page_size"],
            last=False,
        )


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


def run_batch(session, connector, **kwargs):
    return run_batch_ingestion(
        session,
        connector=connector,
        period_start="2026-08-01T00:00:00.000Z",
        period_end="2026-08-31T23:59:59.999Z",
        **dummy_dependencies(),
        **kwargs,
    )


def test_batch_completes_multiple_products_in_one_run(monkeypatch) -> None:
    session = FakeSession()
    connector = FakeConnector([[product(10), product(20), product(30)]])
    calls = install_product_processor(monkeypatch)

    result = run_batch(
        session,
        connector,
        page_size=25,
    )

    assert result.run_id == 1
    assert result.run_status == "completed"
    assert result.pages_fetched == 1
    assert result.discovered_count == 3
    assert result.duplicate_count == 0
    assert result.processed_count == 3
    assert result.ready_count == 3
    assert result.failed_count == 0
    assert result.stopped_by_page_limit is False
    assert result.stopped_by_product_limit is False
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


def test_multi_page_discovery_processes_pages_in_order(monkeypatch) -> None:
    session = FakeSession()
    connector = FakeConnector([
        [product(10), product(20)],
        [product(30), product(40)],
        [product(50)],
    ])
    calls = install_product_processor(monkeypatch)

    result = run_batch(
        session,
        connector,
        page_size=2,
        max_pages=None,
    )

    assert result.pages_fetched == 3
    assert result.discovered_count == 5
    assert result.processed_count == 5
    assert result.ready_count == 5
    assert calls == [10, 20, 30, 40, 50]
    assert [call["page"] for call in connector.discovery_calls] == [1, 2, 3]


def test_duplicate_product_across_pages_is_processed_once(monkeypatch) -> None:
    session = FakeSession()
    connector = FakeConnector([
        [product(10), product(20)],
        [product(20), product(30)],
    ])
    calls = install_product_processor(monkeypatch)

    result = run_batch(
        session,
        connector,
        max_pages=None,
    )

    assert result.pages_fetched == 2
    assert result.discovered_count == 3
    assert result.duplicate_count == 1
    assert result.processed_count == 3
    assert calls == [10, 20, 30]


def test_max_pages_stops_before_fetching_next_page(monkeypatch) -> None:
    session = FakeSession()
    connector = FakeConnector([
        [product(10)],
        [product(20)],
        [product(30)],
    ])
    calls = install_product_processor(monkeypatch)

    result = run_batch(
        session,
        connector,
        max_pages=2,
    )

    assert result.pages_fetched == 2
    assert result.discovered_count == 2
    assert result.stopped_by_page_limit is True
    assert result.stopped_by_product_limit is False
    assert calls == [10, 20]
    assert [call["page"] for call in connector.discovery_calls] == [1, 2]


def test_max_products_stops_inside_page_without_processing_extra(
    monkeypatch,
) -> None:
    session = FakeSession()
    connector = FakeConnector([
        [product(10), product(20)],
        [product(30), product(40)],
    ])
    calls = install_product_processor(monkeypatch)

    result = run_batch(
        session,
        connector,
        max_pages=None,
        max_products=3,
    )

    assert result.pages_fetched == 2
    assert result.discovered_count == 3
    assert result.processed_count == 3
    assert result.stopped_by_product_limit is True
    assert result.stopped_by_page_limit is False
    assert calls == [10, 20, 30]


def test_product_failure_is_isolated_and_batch_continues_across_pages(
    monkeypatch,
) -> None:
    session = FakeSession()
    connector = FakeConnector([
        [product(10), product(20)],
        [product(30)],
    ])
    calls = install_product_processor(monkeypatch, failing_ids={20})

    result = run_batch(
        session,
        connector,
        max_pages=None,
    )

    assert calls == [10, 20, 30]
    assert result.run_status == "failed"
    assert result.pages_fetched == 2
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
    connector = FakeConnector([[]])
    calls = install_product_processor(monkeypatch)

    result = run_batch(session, connector)

    assert calls == []
    assert result.run_status == "completed"
    assert result.pages_fetched == 1
    assert result.discovered_count == 0
    assert result.processed_count == 0
    assert result.ready_count == 0
    assert result.failed_count == 0
    assert result.items == ()


def test_later_page_discovery_failure_preserves_prior_products_and_fails_run(
    monkeypatch,
) -> None:
    session = FakeSession()
    connector = FailingPageConnector(
        [[product(10)], [product(20)]],
        failing_page=2,
    )
    calls = install_product_processor(monkeypatch)

    with pytest.raises(
        BatchIngestionError,
        match="controlled discovery failure",
    ):
        run_batch(
            session,
            connector,
            max_pages=None,
        )

    assert calls == [10]
    assert session.runs[1].status == "failed"
    assert session.rollbacks == 1


def test_inconsistent_pagination_fails_run(monkeypatch) -> None:
    session = FakeSession()
    connector = InconsistentPaginationConnector([[product(10)]])
    install_product_processor(monkeypatch)

    with pytest.raises(
        BatchIngestionError,
        match="last=false on final page",
    ):
        run_batch(
            session,
            connector,
            max_pages=None,
        )

    assert session.runs[1].status == "failed"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"page_size": 0}, "page_size"),
        ({"max_pages": 0}, "max_pages"),
        ({"max_products": 0}, "max_products"),
    ],
)
def test_invalid_limits_are_rejected_before_run_creation(
    kwargs,
    message,
) -> None:
    session = FakeSession()

    with pytest.raises(ValueError, match=message):
        run_batch(
            session,
            FakeConnector([[]]),
            **kwargs,
        )

    assert session.runs == {}
    assert session.commits == 0
