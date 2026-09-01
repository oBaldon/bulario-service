from pathlib import Path

import pytest

from bulario_service.anvisa import (
    BulaVersion,
    DiscoveryPage,
    DiscoveredProduct,
    ProductDetail,
)
from bulario_service.anvisa_documents import DownloadedBulaDocument
from bulario_service.document_storage import StoredBulaDocument
from bulario_service.document_text import ExtractedBulaText
from bulario_service.e2e_pipeline import (
    E2EPipelineError,
    run_single_product_pipeline,
)
from bulario_service.models import IngestionItem, IngestionRun
from bulario_service.publication_publisher import PublishResult


class FakeSession:
    def __init__(self):
        self._next_run_id = 1
        self._next_item_id = 1
        self.runs = {}
        self.items = {}
        self.commits = 0
        self.rollbacks = 0
        self.pending_publication = False

    def add(self, entity):
        if isinstance(entity, IngestionRun):
            if entity.id is None:
                entity.id = self._next_run_id
                self._next_run_id += 1
            self.runs[entity.id] = entity
        elif isinstance(entity, IngestionItem):
            if entity.id is None:
                entity.id = self._next_item_id
                self._next_item_id += 1
            self.items[entity.id] = entity

    def flush(self):
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        self.pending_publication = False

    def get(self, model, entity_id):
        if model is IngestionRun:
            return self.runs.get(entity_id)
        if model is IngestionItem:
            return self.items.get(entity_id)
        return None


class FakeConnector:
    def __init__(self):
        self.product = DiscoveredProduct(
            source_product_id=1174609,
            registration_number="123",
            product_name="Produto",
            current_expedient="456",
            company_name="Empresa",
            company_cnpj="00000000000000",
            process_number="25351",
            publication_date="28/08/2026",
            raw_payload={"idProduto": 1174609},
        )
        self.version = BulaVersion(
            source_document_id=35480554,
            expedient="456",
            registration_number="123",
            publication_date="28/08/2026",
            status="Publicado",
            patient_token="patient-token",
            professional_token="professional-token",
            current=True,
            raw_payload={"idDocumento": 35480554},
        )

    def discover_page(self, **kwargs):
        return DiscoveryPage(
            items=(self.product,),
            total_elements=1,
            total_pages=1,
            page=1,
            page_size=1,
            last=True,
        )

    def get_product_detail(self, source_product_id):
        return ProductDetail(
            source_product_id=source_product_id,
            registration_number="123",
            product_name="Produto",
            versions=(self.version,),
        )


class FakeDownloader:
    def download(self, *, source_document_id, kind, token):
        content = f"%PDF-{kind}".encode()
        return DownloadedBulaDocument(
            source_document_id=source_document_id,
            kind=kind,
            content=content,
            size_bytes=len(content),
            sha256=("a" if kind == "patient" else "b") * 64,
            content_type="application/pdf",
        )


class FailingDownloader(FakeDownloader):
    def download(self, **kwargs):
        raise RuntimeError("controlled download failure")


class FakeStorage:
    def store(self, *, source_product_id, document):
        return StoredBulaDocument(
            source_product_id=source_product_id,
            source_document_id=document.source_document_id,
            kind=document.kind,
            storage_key=(
                f"bulas/{source_product_id}/"
                f"{document.source_document_id}/{document.kind}.pdf"
            ),
            sha256=document.sha256,
            size_bytes=document.size_bytes,
        )

    def resolve(self, storage_key):
        return Path("/tmp") / storage_key


class FakeExtractor:
    def extract(self, *, pdf_path, stored_document):
        text = f"Texto {stored_document.kind}"
        return ExtractedBulaText(
            source_product_id=stored_document.source_product_id,
            source_document_id=stored_document.source_document_id,
            kind=stored_document.kind,
            document_storage_key=stored_document.storage_key,
            document_sha256=stored_document.sha256,
            text=text,
            text_sha256="c" * 64,
            character_count=len(text),
        )


class FailingExtractor(FakeExtractor):
    def extract(self, **kwargs):
        raise RuntimeError("controlled extraction failure")


def install_persistence_stubs(monkeypatch, *, publish_action="inserted"):
    calls = {
        "operational": 0,
        "text": 0,
        "candidate": 0,
        "publish": 0,
    }

    def operational(*args, **kwargs):
        calls["operational"] += 1
        return object()

    def text_persistence(*args, **kwargs):
        calls["text"] += 1
        return object()

    def candidate(*args, **kwargs):
        calls["candidate"] += 1
        return object()

    def publish(session, *, candidate):
        calls["publish"] += 1
        return PublishResult(
            action=publish_action,
            row_id=2,
            source_record_id="anvisa:1174609:35480554",
        )

    monkeypatch.setattr(
        "bulario_service.e2e_pipeline.persist_operational_version",
        operational,
    )
    monkeypatch.setattr(
        "bulario_service.e2e_pipeline.persist_text_artifact",
        text_persistence,
    )
    monkeypatch.setattr(
        "bulario_service.e2e_pipeline.build_publication_candidate",
        candidate,
    )

    return calls, publish


def test_controlled_e2e_completes_ready_and_publishes(monkeypatch) -> None:
    session = FakeSession()
    calls, publish = install_persistence_stubs(monkeypatch)

    result = run_single_product_pipeline(
        session,
        connector=FakeConnector(),
        downloader=FakeDownloader(),
        storage=FakeStorage(),
        extractor=FakeExtractor(),
        period_start="2026-08-28T00:00:00.000Z",
        period_end="2026-08-29T00:00:00.000Z",
        publish=publish,
    )

    run = session.runs[result.run_id]
    item = session.items[result.item_id]

    assert result.publish_action == "inserted"
    assert result.public_row_id == 2
    assert run.status == "completed"
    assert item.status == "ready"
    assert item.source_record_id == "anvisa-product:1174609"
    assert item.source_fingerprint is not None
    assert item.normalized_payload["source_document_id"] == 35480554
    assert calls == {
        "operational": 1,
        "text": 2,
        "candidate": 1,
        "publish": 1,
    }
    assert session.rollbacks == 0
    assert session.commits == 6


def test_rerun_can_surface_publisher_unchanged(monkeypatch) -> None:
    session = FakeSession()
    _, publish = install_persistence_stubs(
        monkeypatch,
        publish_action="unchanged",
    )

    result = run_single_product_pipeline(
        session,
        connector=FakeConnector(),
        downloader=FakeDownloader(),
        storage=FakeStorage(),
        extractor=FakeExtractor(),
        period_start="2026-08-28T00:00:00.000Z",
        period_end="2026-08-29T00:00:00.000Z",
        publish=publish,
    )

    assert result.publish_action == "unchanged"
    assert session.items[result.item_id].status == "ready"


def test_download_failure_marks_item_and_run_failed(monkeypatch) -> None:
    session = FakeSession()
    calls, publish = install_persistence_stubs(monkeypatch)

    with pytest.raises(
        E2EPipelineError,
        match="controlled download failure",
    ):
        run_single_product_pipeline(
            session,
            connector=FakeConnector(),
            downloader=FailingDownloader(),
            storage=FakeStorage(),
            extractor=FakeExtractor(),
            period_start="2026-08-28T00:00:00.000Z",
            period_end="2026-08-29T00:00:00.000Z",
            publish=publish,
        )

    run = session.runs[1]
    item = session.items[1]

    assert run.status == "failed"
    assert item.status == "failed"
    assert item.error_code == "RuntimeError"
    assert "controlled download failure" in item.error_message
    assert calls["publish"] == 0
    assert session.rollbacks == 1


def test_missing_current_document_marks_item_failed(monkeypatch) -> None:
    session = FakeSession()
    calls, publish = install_persistence_stubs(monkeypatch)
    connector = FakeConnector()
    connector.version = BulaVersion(
        **{
            **connector.version.__dict__,
            "current": False,
        }
    )

    with pytest.raises(
        E2EPipelineError,
        match="exactly one current version",
    ):
        run_single_product_pipeline(
            session,
            connector=connector,
            downloader=FakeDownloader(),
            storage=FakeStorage(),
            extractor=FakeExtractor(),
            period_start="2026-08-28T00:00:00.000Z",
            period_end="2026-08-29T00:00:00.000Z",
            publish=publish,
        )

    assert session.runs[1].status == "failed"
    assert session.items[1].status == "failed"
    assert calls["publish"] == 0


def test_missing_required_pdf_token_marks_item_failed(monkeypatch) -> None:
    session = FakeSession()
    calls, publish = install_persistence_stubs(monkeypatch)
    connector = FakeConnector()
    connector.version = BulaVersion(
        **{
            **connector.version.__dict__,
            "professional_token": None,
        }
    )

    with pytest.raises(
        E2EPipelineError,
        match="missing required document token",
    ):
        run_single_product_pipeline(
            session,
            connector=connector,
            downloader=FakeDownloader(),
            storage=FakeStorage(),
            extractor=FakeExtractor(),
            period_start="2026-08-28T00:00:00.000Z",
            period_end="2026-08-29T00:00:00.000Z",
            publish=publish,
        )

    assert session.runs[1].status == "failed"
    assert session.items[1].status == "failed"
    assert calls["publish"] == 0



def test_extraction_failure_marks_item_failed_without_publication(
    monkeypatch,
) -> None:
    session = FakeSession()
    calls, publish = install_persistence_stubs(monkeypatch)

    with pytest.raises(
        E2EPipelineError,
        match="controlled extraction failure",
    ):
        run_single_product_pipeline(
            session,
            connector=FakeConnector(),
            downloader=FakeDownloader(),
            storage=FakeStorage(),
            extractor=FailingExtractor(),
            period_start="2026-08-28T00:00:00.000Z",
            period_end="2026-08-29T00:00:00.000Z",
            publish=publish,
        )

    assert session.runs[1].status == "failed"
    assert session.items[1].status == "failed"
    assert session.items[1].error_code == "RuntimeError"
    assert calls["publish"] == 0
    assert session.rollbacks == 1


def test_failure_after_publication_attempt_rolls_back_and_marks_failed(
    monkeypatch,
) -> None:
    session = FakeSession()
    calls, _ = install_persistence_stubs(monkeypatch)

    def publish_then_fail(session, *, candidate):
        calls["publish"] += 1
        session.pending_publication = True
        raise RuntimeError("controlled post-publication failure")

    with pytest.raises(
        E2EPipelineError,
        match="controlled post-publication failure",
    ):
        run_single_product_pipeline(
            session,
            connector=FakeConnector(),
            downloader=FakeDownloader(),
            storage=FakeStorage(),
            extractor=FakeExtractor(),
            period_start="2026-08-28T00:00:00.000Z",
            period_end="2026-08-29T00:00:00.000Z",
            publish=publish_then_fail,
        )

    assert calls["publish"] == 1
    assert session.pending_publication is False
    assert session.rollbacks == 1
    assert session.runs[1].status == "failed"
    assert session.items[1].status == "failed"


class HistoricalConnector(FakeConnector):
    def __init__(self):
        super().__init__()
        self.historical_versions = (
            BulaVersion(
                source_document_id=30415878,
                expedient="hist-1",
                registration_number="123",
                publication_date="17/05/2023",
                status="Publicado",
                patient_token="historical-patient-1",
                professional_token="historical-professional-1",
                current=False,
                raw_payload={"idDocumento": 30415878},
            ),
            BulaVersion(
                source_document_id=24619848,
                expedient="hist-2",
                registration_number="123",
                publication_date="23/04/2021",
                status="Publicado",
                patient_token="historical-patient-2",
                professional_token=None,
                current=False,
                raw_payload={"idDocumento": 24619848},
            ),
        )

    def get_product_detail(self, source_product_id):
        return ProductDetail(
            source_product_id=source_product_id,
            registration_number="123",
            product_name="Produto",
            versions=(
                self.version,
                *self.historical_versions,
            ),
        )


class RecordingDownloader(FakeDownloader):
    def __init__(self):
        self.calls = []

    def download(self, *, source_document_id, kind, token):
        self.calls.append((source_document_id, kind, token))
        return super().download(
            source_document_id=source_document_id,
            kind=kind,
            token=token,
        )


def test_product_pipeline_persists_complete_history_and_publishes_current_only(
    monkeypatch,
) -> None:
    session = FakeSession()
    connector = HistoricalConnector()
    downloader = RecordingDownloader()
    persisted_versions = []
    candidate_document_ids = []
    text_document_ids = []

    def operational(
        session,
        *,
        product,
        version,
        stored_documents,
        ingestion_item,
    ):
        persisted_versions.append(
            (
                version.source_document_id,
                version.current,
                tuple(document.kind for document in stored_documents),
            )
        )
        return object()

    def text_persistence(session, *, extracted):
        text_document_ids.append(extracted.source_document_id)
        return object()

    def candidate(session, *, source_document_id):
        candidate_document_ids.append(source_document_id)
        return object()

    monkeypatch.setattr(
        "bulario_service.e2e_pipeline.persist_operational_version",
        operational,
    )
    monkeypatch.setattr(
        "bulario_service.e2e_pipeline.persist_text_artifact",
        text_persistence,
    )
    monkeypatch.setattr(
        "bulario_service.e2e_pipeline.build_publication_candidate",
        candidate,
    )

    def publish(session, *, candidate):
        return PublishResult(
            action="inserted",
            row_id=2,
            source_record_id="anvisa:1174609:35480554",
        )

    result = run_single_product_pipeline(
        session,
        connector=connector,
        downloader=downloader,
        storage=FakeStorage(),
        extractor=FakeExtractor(),
        period_start="2026-08-28T00:00:00.000Z",
        period_end="2026-08-29T00:00:00.000Z",
        publish=publish,
    )

    assert result.source_document_id == 35480554
    assert persisted_versions == [
        (30415878, False, ("patient", "professional")),
        (24619848, False, ("patient",)),
        (35480554, True, ("patient", "professional")),
    ]
    assert downloader.calls == [
        (30415878, "patient", "historical-patient-1"),
        (30415878, "professional", "historical-professional-1"),
        (24619848, "patient", "historical-patient-2"),
        (35480554, "patient", "patient-token"),
        (35480554, "professional", "professional-token"),
    ]
    assert text_document_ids == [
        30415878,
        30415878,
        24619848,
        35480554,
        35480554,
    ]
    assert candidate_document_ids == [35480554]
    assert session.items[result.item_id].normalized_payload["version_count"] == 3


def test_duplicate_history_document_id_fails_before_download(
    monkeypatch,
) -> None:
    session = FakeSession()
    connector = HistoricalConnector()
    connector.historical_versions = (
        BulaVersion(
            **{
                **connector.version.__dict__,
                "current": False,
            }
        ),
    )
    downloader = RecordingDownloader()
    calls, publish = install_persistence_stubs(monkeypatch)

    with pytest.raises(
        E2EPipelineError,
        match="duplicate source_document_id",
    ):
        run_single_product_pipeline(
            session,
            connector=connector,
            downloader=downloader,
            storage=FakeStorage(),
            extractor=FakeExtractor(),
            period_start="2026-08-28T00:00:00.000Z",
            period_end="2026-08-29T00:00:00.000Z",
            publish=publish,
        )

    assert downloader.calls == []
    assert calls["operational"] == 0
    assert calls["publish"] == 0
    assert session.items[1].status == "failed"
