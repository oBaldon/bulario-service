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
        self.terminal_product_ids = set()
        self.failed_product_ids = set()

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


def install_batch_fakes(monkeypatch, session, *, failing_ids=()):
    calls = []
    failing_ids = set(failing_ids)

    def process(_session, *, run, product, **kwargs):
        calls.append(product.source_product_id)
        session.terminal_product_ids.add(product.source_product_id)
        if product.source_product_id in failing_ids:
            session.failed_product_ids.add(product.source_product_id)
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
    monkeypatch.setattr(
        "bulario_service.batch_ingestion._load_terminal_product_ids",
        lambda _session, *, run_id: set(session.terminal_product_ids),
    )
    monkeypatch.setattr(
        "bulario_service.batch_ingestion._count_items_with_status",
        lambda _session, *, run_id, status: (
            len(session.failed_product_ids) if status == "failed" else 0
        ),
    )
    monkeypatch.setattr(
        "bulario_service.batch_ingestion._load_retryable_failed_items",
        lambda _session, *, run_id, max_product_retries: (),
    )
    monkeypatch.setattr(
        "bulario_service.batch_ingestion._get_product_item",
        lambda _session, *, run_id, source_product_id: None,
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
        period_start=kwargs.pop(
            "period_start",
            "2026-08-01T00:00:00.000Z",
        ),
        period_end=kwargs.pop(
            "period_end",
            "2026-08-31T23:59:59.999Z",
        ),
        **dummy_dependencies(),
        **kwargs,
    )


def test_batch_completes_single_page_run(monkeypatch) -> None:
    session = FakeSession()
    connector = FakeConnector([[product(10), product(20)]])
    calls = install_batch_fakes(monkeypatch, session)

    result = run_batch(
        session,
        connector,
        page_size=25,
    )

    assert result.run_status == "completed"
    assert result.resumed is False
    assert result.start_page == 1
    assert result.last_completed_page == 1
    assert result.pages_fetched == 1
    assert result.discovered_count == 2
    assert result.skipped_terminal_count == 0
    assert result.processed_count == 2
    assert calls == [10, 20]
    run = session.runs[result.run_id]
    assert run.mode == "batch"
    assert run.page_size == 25
    assert run.period_start == "2026-08-01T00:00:00.000Z"
    assert run.period_end == "2026-08-31T23:59:59.999Z"


def test_multi_page_discovery_completes_and_checkpoints(monkeypatch) -> None:
    session = FakeSession()
    connector = FakeConnector([
        [product(10), product(20)],
        [product(30), product(40)],
        [product(50)],
    ])
    calls = install_batch_fakes(monkeypatch, session)

    result = run_batch(
        session,
        connector,
        page_size=2,
        max_pages=None,
    )

    assert result.run_status == "completed"
    assert result.pages_fetched == 3
    assert result.last_completed_page == 3
    assert result.discovered_count == 5
    assert calls == [10, 20, 30, 40, 50]
    assert session.runs[result.run_id].last_checkpoint_at is not None


def test_duplicate_product_across_pages_is_processed_once(monkeypatch) -> None:
    session = FakeSession()
    connector = FakeConnector([
        [product(10), product(20)],
        [product(20), product(30)],
    ])
    calls = install_batch_fakes(monkeypatch, session)

    result = run_batch(
        session,
        connector,
        max_pages=None,
    )

    assert result.duplicate_count == 1
    assert result.processed_count == 3
    assert calls == [10, 20, 30]


def test_max_pages_pauses_run_after_checkpoint(monkeypatch) -> None:
    session = FakeSession()
    connector = FakeConnector([
        [product(10)],
        [product(20)],
        [product(30)],
    ])
    calls = install_batch_fakes(monkeypatch, session)

    result = run_batch(
        session,
        connector,
        max_pages=2,
    )

    assert result.run_status == "paused"
    assert result.last_completed_page == 2
    assert result.stopped_by_page_limit is True
    assert calls == [10, 20]
    assert [call["page"] for call in connector.discovery_calls] == [1, 2]


def test_resume_uses_persisted_window_and_starts_next_page(monkeypatch) -> None:
    session = FakeSession()
    first_connector = FakeConnector([
        [product(10)],
        [product(20)],
        [product(30)],
    ])
    calls = install_batch_fakes(monkeypatch, session)

    first = run_batch(
        session,
        first_connector,
        page_size=1,
        max_pages=1,
    )

    assert first.run_status == "paused"
    assert first.last_completed_page == 1
    assert calls == [10]

    second_connector = FakeConnector([
        [product(999)],
        [product(20)],
        [product(30)],
    ])
    resumed = run_batch_ingestion(
        session,
        connector=second_connector,
        downloader=SimpleNamespace(),
        storage=SimpleNamespace(),
        extractor=SimpleNamespace(),
        period_start=None,
        period_end=None,
        page_size=None,
        max_pages=None,
        resume_run_id=first.run_id,
    )

    assert resumed.run_id == first.run_id
    assert resumed.resumed is True
    assert resumed.start_page == 2
    assert resumed.last_completed_page == 3
    assert resumed.run_status == "completed"
    assert calls == [10, 20, 30]
    assert [call["page"] for call in second_connector.discovery_calls] == [2, 3]
    assert second_connector.discovery_calls[0]["page_size"] == 1
    assert second_connector.discovery_calls[0]["period_start"] == (
        "2026-08-01T00:00:00.000Z"
    )


def test_resume_mid_page_skips_terminal_items(monkeypatch) -> None:
    session = FakeSession()
    connector = FakeConnector([
        [product(10), product(20), product(30)],
    ])
    calls = install_batch_fakes(monkeypatch, session)

    first = run_batch(
        session,
        connector,
        page_size=3,
        max_pages=None,
        max_products=1,
    )

    assert first.run_status == "paused"
    assert first.last_completed_page == 0
    assert calls == [10]

    resumed_connector = FakeConnector([
        [product(10), product(20), product(30)],
    ])
    resumed = run_batch_ingestion(
        session,
        connector=resumed_connector,
        downloader=SimpleNamespace(),
        storage=SimpleNamespace(),
        extractor=SimpleNamespace(),
        period_start=None,
        period_end=None,
        page_size=None,
        max_pages=None,
        max_products=None,
        resume_run_id=first.run_id,
    )

    assert resumed.start_page == 1
    assert resumed.skipped_terminal_count == 1
    assert resumed.last_completed_page == 1
    assert resumed.run_status == "completed"
    assert calls == [10, 20, 30]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "period_start",
            "2026-07-01T00:00:00.000Z",
            "period_start",
        ),
        (
            "period_end",
            "2026-09-01T00:00:00.000Z",
            "period_end",
        ),
        ("page_size", 99, "page_size"),
    ],
)
def test_resume_rejects_incompatible_parameters(
    monkeypatch,
    field,
    value,
    message,
) -> None:
    session = FakeSession()
    calls = install_batch_fakes(monkeypatch, session)
    connector = FakeConnector([[product(10)], [product(20)]])

    first = run_batch(
        session,
        connector,
        page_size=1,
        max_pages=1,
    )
    assert first.run_status == "paused"

    kwargs = {
        "period_start": None,
        "period_end": None,
        "page_size": None,
    }
    kwargs[field] = value

    with pytest.raises(BatchIngestionError, match=message):
        run_batch_ingestion(
            session,
            connector=connector,
            downloader=SimpleNamespace(),
            storage=SimpleNamespace(),
            extractor=SimpleNamespace(),
            max_pages=1,
            resume_run_id=first.run_id,
            **kwargs,
        )

    assert session.runs[first.run_id].status == "paused"
    assert calls == [10]


def test_completed_run_cannot_be_resumed(monkeypatch) -> None:
    session = FakeSession()
    install_batch_fakes(monkeypatch, session)
    connector = FakeConnector([[product(10)]])

    completed = run_batch(
        session,
        connector,
        max_pages=None,
    )
    assert completed.run_status == "completed"

    with pytest.raises(BatchIngestionError, match="not resumable"):
        run_batch_ingestion(
            session,
            connector=connector,
            downloader=SimpleNamespace(),
            storage=SimpleNamespace(),
            extractor=SimpleNamespace(),
            period_start=None,
            period_end=None,
            resume_run_id=completed.run_id,
        )


def test_product_failure_is_isolated_and_final_run_fails(monkeypatch) -> None:
    session = FakeSession()
    connector = FakeConnector([
        [product(10), product(20)],
        [product(30)],
    ])
    calls = install_batch_fakes(
        monkeypatch,
        session,
        failing_ids={20},
    )

    result = run_batch(
        session,
        connector,
        max_pages=None,
    )

    assert calls == [10, 20, 30]
    assert result.run_status == "failed"
    assert result.ready_count == 2
    assert result.failed_count == 1
    assert result.last_completed_page == 2


def test_later_page_discovery_failure_preserves_checkpoint_and_fails_run(
    monkeypatch,
) -> None:
    session = FakeSession()
    connector = FailingPageConnector(
        [[product(10)], [product(20)]],
        failing_page=2,
    )
    calls = install_batch_fakes(monkeypatch, session)

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
    assert session.runs[1].last_completed_page == 1
    assert session.runs[1].status == "failed"


def test_inconsistent_pagination_fails_run(monkeypatch) -> None:
    session = FakeSession()
    connector = InconsistentPaginationConnector([[product(10)]])
    install_batch_fakes(monkeypatch, session)

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


def test_new_run_requires_window_before_creation(monkeypatch) -> None:
    session = FakeSession()
    install_batch_fakes(monkeypatch, session)

    with pytest.raises(ValueError, match="period_start and period_end"):
        run_batch_ingestion(
            session,
            connector=FakeConnector([[]]),
            downloader=SimpleNamespace(),
            storage=SimpleNamespace(),
            extractor=SimpleNamespace(),
            period_start=None,
            period_end=None,
        )

    assert session.runs == {}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"page_size": 0}, "page_size"),
        ({"max_pages": 0}, "max_pages"),
        ({"max_products": 0}, "max_products"),
    ],
)
def test_invalid_limits_are_rejected_before_run_creation(
    monkeypatch,
    kwargs,
    message,
) -> None:
    session = FakeSession()
    install_batch_fakes(monkeypatch, session)

    with pytest.raises(ValueError, match=message):
        run_batch(
            session,
            FakeConnector([[]]),
            **kwargs,
        )

    assert session.runs == {}



def test_full_mode_is_persisted_and_resumed_only_as_full(monkeypatch) -> None:
    session = FakeSession()
    install_batch_fakes(monkeypatch, session)
    connector = FakeConnector([
        [product(10)],
        [product(20)],
    ])

    first = run_batch_ingestion(
        session,
        connector=connector,
        downloader=SimpleNamespace(),
        storage=SimpleNamespace(),
        extractor=SimpleNamespace(),
        period_start="2026-01-01T00:00:00.000Z",
        period_end="2026-08-31T23:59:59.999Z",
        page_size=1,
        max_pages=1,
        run_mode="full",
    )

    assert first.run_status == "paused"
    assert session.runs[first.run_id].mode == "full"

    resumed = run_batch_ingestion(
        session,
        connector=connector,
        downloader=SimpleNamespace(),
        storage=SimpleNamespace(),
        extractor=SimpleNamespace(),
        period_start=None,
        period_end=None,
        page_size=None,
        max_pages=None,
        resume_run_id=first.run_id,
        run_mode="full",
    )

    assert resumed.run_id == first.run_id
    assert resumed.run_status == "completed"
    assert resumed.start_page == 2


def test_full_run_cannot_be_resumed_as_batch(monkeypatch) -> None:
    session = FakeSession()
    install_batch_fakes(monkeypatch, session)
    connector = FakeConnector([
        [product(10)],
        [product(20)],
    ])

    first = run_batch_ingestion(
        session,
        connector=connector,
        downloader=SimpleNamespace(),
        storage=SimpleNamespace(),
        extractor=SimpleNamespace(),
        period_start="2026-01-01T00:00:00.000Z",
        period_end="2026-08-31T23:59:59.999Z",
        page_size=1,
        max_pages=1,
        run_mode="full",
    )

    with pytest.raises(BatchIngestionError, match="requested resume"):
        run_batch_ingestion(
            session,
            connector=connector,
            downloader=SimpleNamespace(),
            storage=SimpleNamespace(),
            extractor=SimpleNamespace(),
            period_start=None,
            period_end=None,
            resume_run_id=first.run_id,
            run_mode="batch",
        )


def test_invalid_run_mode_is_rejected_before_run_creation(monkeypatch) -> None:
    session = FakeSession()
    install_batch_fakes(monkeypatch, session)

    with pytest.raises(ValueError, match="run_mode"):
        run_batch_ingestion(
            session,
            connector=FakeConnector([[]]),
            downloader=SimpleNamespace(),
            storage=SimpleNamespace(),
            extractor=SimpleNamespace(),
            period_start="2026-01-01T00:00:00.000Z",
            period_end="2026-08-31T23:59:59.999Z",
            run_mode="unknown",
        )

    assert session.runs == {}



def test_incremental_mode_is_persisted_and_resumable(monkeypatch) -> None:
    session = FakeSession()
    install_batch_fakes(monkeypatch, session)
    connector = FakeConnector([
        [product(10)],
        [product(20)],
    ])

    first = run_batch_ingestion(
        session,
        connector=connector,
        downloader=SimpleNamespace(),
        storage=SimpleNamespace(),
        extractor=SimpleNamespace(),
        period_start="2026-08-24T00:00:00.000Z",
        period_end="2026-08-31T00:00:00.000Z",
        page_size=1,
        max_pages=1,
        run_mode="incremental",
    )

    assert first.run_status == "paused"
    assert first.run_mode == "incremental"
    assert first.period_start == "2026-08-24T00:00:00.000Z"
    assert session.runs[first.run_id].mode == "incremental"

    resumed = run_batch_ingestion(
        session,
        connector=connector,
        downloader=SimpleNamespace(),
        storage=SimpleNamespace(),
        extractor=SimpleNamespace(),
        period_start=None,
        period_end=None,
        page_size=None,
        max_pages=None,
        resume_run_id=first.run_id,
        run_mode="incremental",
    )

    assert resumed.run_id == first.run_id
    assert resumed.run_status == "completed"
    assert resumed.run_mode == "incremental"
    assert resumed.period_end == "2026-08-31T00:00:00.000Z"



def test_transient_product_failure_retries_same_item_and_recovers(
    monkeypatch,
) -> None:
    from bulario_service.anvisa import AnvisaTransientSourceError
    from bulario_service.models import IngestionItem

    session = FakeSession()
    connector = FakeConnector([[product(4729)]])
    item = IngestionItem(
        id=56,
        run_id=1,
        source_record_id="anvisa-product:4729",
        status="failed",
        error_code="AnvisaTransientSourceError",
        error_message="ANVISA returned HTTP 500",
        error_class="transient",
        retry_count=0,
        raw_payload=product(4729).raw_payload,
    )
    calls = []

    def process(_session, *, retry_item=None, product, **kwargs):
        calls.append(retry_item)
        if retry_item is None:
            item.status = "failed"
            raise AnvisaTransientSourceError(
                "ANVISA returned HTTP 500 "
                "path=/api/consulta/bulario/4729 page=2"
            )

        item.retry_count += 1
        item.status = "ready"
        item.error_code = None
        item.error_message = None
        item.error_class = None
        return ProcessedProductResult(
            item_id=56,
            source_product_id=4729,
            source_document_id=35474505,
            publish_action="inserted",
            public_row_id=47,
        )

    monkeypatch.setattr(
        "bulario_service.batch_ingestion.process_discovered_product",
        process,
    )
    monkeypatch.setattr(
        "bulario_service.batch_ingestion._get_product_item",
        lambda _session, *, run_id, source_product_id: item,
    )
    monkeypatch.setattr(
        "bulario_service.batch_ingestion._load_terminal_product_ids",
        lambda _session, *, run_id: set(),
    )
    monkeypatch.setattr(
        "bulario_service.batch_ingestion._count_items_with_status",
        lambda _session, *, run_id, status: 0,
    )
    monkeypatch.setattr(
        "bulario_service.batch_ingestion.time.sleep",
        lambda seconds: None,
    )

    result = run_batch_ingestion(
        session,
        connector=connector,
        downloader=SimpleNamespace(),
        storage=SimpleNamespace(),
        extractor=SimpleNamespace(),
        period_start="2026-08-23T00:00:00.000Z",
        period_end="2026-08-31T00:00:00.000Z",
        max_pages=None,
        max_product_retries=2,
        retry_backoff_seconds=2.0,
    )

    assert result.run_status == "completed"
    assert result.retry_count == 1
    assert result.ready_count == 1
    assert result.failed_count == 0
    assert len(calls) == 2
    assert calls[0] is None
    assert calls[1] is item


def test_source_blocked_product_pauses_without_blind_retry(
    monkeypatch,
) -> None:
    from bulario_service.anvisa import AnvisaAccessDeniedError
    from bulario_service.models import IngestionItem

    session = FakeSession()
    connector = FakeConnector([[product(10), product(20)]])
    item = IngestionItem(
        id=110,
        run_id=1,
        source_record_id="anvisa-product:10",
        status="failed",
        error_code="AnvisaAccessDeniedError",
        error_message="ANVISA returned HTTP 403",
        error_class="source_blocked",
        retry_count=0,
        raw_payload=product(10).raw_payload,
    )
    calls = []

    def process(_session, *, product, **kwargs):
        calls.append(product.source_product_id)
        raise AnvisaAccessDeniedError("ANVISA returned HTTP 403")

    monkeypatch.setattr(
        "bulario_service.batch_ingestion.process_discovered_product",
        process,
    )
    monkeypatch.setattr(
        "bulario_service.batch_ingestion._get_product_item",
        lambda _session, *, run_id, source_product_id: item,
    )
    monkeypatch.setattr(
        "bulario_service.batch_ingestion._load_terminal_product_ids",
        lambda _session, *, run_id: set(),
    )
    monkeypatch.setattr(
        "bulario_service.batch_ingestion._count_items_with_status",
        lambda _session, *, run_id, status: 1,
    )

    result = run_batch_ingestion(
        session,
        connector=connector,
        downloader=SimpleNamespace(),
        storage=SimpleNamespace(),
        extractor=SimpleNamespace(),
        period_start="2026-08-23T00:00:00.000Z",
        period_end="2026-08-31T00:00:00.000Z",
        max_pages=None,
    )

    assert result.run_status == "paused"
    assert result.stopped_by_source_blocked is True
    assert result.retry_count == 0
    assert calls == [10]


def test_legacy_failed_item_can_be_reconstructed_for_resume() -> None:
    from bulario_service.batch_ingestion import _product_from_failed_item
    from bulario_service.models import IngestionItem

    raw = product(4729).raw_payload | {
        "nomeProduto": "Produto 4729",
        "numeroRegistro": "4729",
        "razaoSocial": "Empresa",
    }
    item = IngestionItem(
        id=56,
        run_id=8,
        source_record_id="anvisa-product:4729",
        status="failed",
        error_code="AnvisaSourceError",
        error_message="ANVISA returned HTTP 500",
        retry_count=0,
        raw_payload=raw,
    )

    restored = _product_from_failed_item(item)

    assert restored.source_product_id == 4729
    assert restored.product_name == "Produto 4729"
    assert restored.registration_number == "4729"
    assert restored.raw_payload == raw



def test_reconciliation_mode_is_persisted_and_resumable(
    monkeypatch,
) -> None:
    session = FakeSession()
    install_batch_fakes(monkeypatch, session)
    connector = FakeConnector([
        [product(10)],
        [product(20)],
    ])

    first = run_batch_ingestion(
        session,
        connector=connector,
        downloader=SimpleNamespace(),
        storage=SimpleNamespace(),
        extractor=SimpleNamespace(),
        period_start="2026-01-01T00:00:00.000Z",
        period_end="2026-08-31T23:59:59.999Z",
        page_size=1,
        max_pages=1,
        run_mode="reconciliation",
    )

    assert first.run_status == "paused"
    assert first.run_mode == "reconciliation"
    assert session.runs[first.run_id].mode == "reconciliation"

    resumed = run_batch_ingestion(
        session,
        connector=connector,
        downloader=SimpleNamespace(),
        storage=SimpleNamespace(),
        extractor=SimpleNamespace(),
        period_start=None,
        period_end=None,
        page_size=None,
        max_pages=None,
        resume_run_id=first.run_id,
        run_mode="reconciliation",
    )

    assert resumed.run_id == first.run_id
    assert resumed.run_status == "completed"
    assert resumed.start_page == 2


def test_reconciliation_run_cannot_be_resumed_as_incremental(
    monkeypatch,
) -> None:
    session = FakeSession()
    install_batch_fakes(monkeypatch, session)
    connector = FakeConnector([
        [product(10)],
        [product(20)],
    ])

    first = run_batch_ingestion(
        session,
        connector=connector,
        downloader=SimpleNamespace(),
        storage=SimpleNamespace(),
        extractor=SimpleNamespace(),
        period_start="2026-01-01T00:00:00.000Z",
        period_end="2026-08-31T23:59:59.999Z",
        page_size=1,
        max_pages=1,
        run_mode="reconciliation",
    )

    with pytest.raises(BatchIngestionError, match="requested resume"):
        run_batch_ingestion(
            session,
            connector=connector,
            downloader=SimpleNamespace(),
            storage=SimpleNamespace(),
            extractor=SimpleNamespace(),
            period_start=None,
            period_end=None,
            resume_run_id=first.run_id,
            run_mode="incremental",
        )
