from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from bulario_service.hardening import (
    HardeningCheckError,
    _different_sha256,
    _expect_publication_conflict,
    find_latest_published_current_document_id,
)
from bulario_service.models import BularioDocumentVersion, BularioProduct
from bulario_service.publication_contract import (
    BulaPublicationCandidate,
    PublicationDocument,
)
from bulario_service.publication_publisher import (
    BulaPublicationConflictError,
)


def candidate() -> BulaPublicationCandidate:
    patient = PublicationDocument(
        kind="patient",
        storage_key="bulas/1/2/patient.pdf",
        document_sha256="a" * 64,
        text_sha256="b" * 64,
        text_content="Texto",
        character_count=5,
        extraction_method="pdftotext-layout-utf8",
        normalization_version="v1",
    )
    professional = PublicationDocument(
        kind="professional",
        storage_key="bulas/1/2/professional.pdf",
        document_sha256="c" * 64,
        text_sha256="d" * 64,
        text_content="Texto",
        character_count=5,
        extraction_method="pdftotext-layout-utf8",
        normalization_version="v1",
    )
    return BulaPublicationCandidate(
        source="ANVISA_BULARIO",
        source_record_id="anvisa:1:2",
        source_url="https://consultas.anvisa.gov.br/#/bulario/",
        source_product_id=1,
        source_document_id=2,
        source_fingerprint="e" * 64,
        ingested_at=datetime.now(timezone.utc),
        ingestion_status="ready",
        registration_number=None,
        product_name="Produto",
        company_name=None,
        company_cnpj=None,
        process_number=None,
        expedient=None,
        source_publication_date=None,
        patient=patient,
        professional=professional,
    )


def test_different_sha256_preserves_valid_shape_and_changes_value() -> None:
    original = "a" * 64
    changed = _different_sha256(original)

    assert changed != original
    assert len(changed) == 64
    assert set(changed) <= set("0123456789abcdef")


def test_expected_publication_conflict_is_accepted(monkeypatch) -> None:
    session = Mock()

    def conflict(*args, **kwargs):
        raise BulaPublicationConflictError(
            "existing row differs fields=source_fingerprint"
        )

    monkeypatch.setattr(
        "bulario_service.hardening.publish_candidate",
        conflict,
    )

    _expect_publication_conflict(
        session,
        candidate=candidate(),
        expected_field="source_fingerprint",
    )

    session.rollback.assert_called_once()


def test_missing_publication_conflict_fails_hardening(monkeypatch) -> None:
    session = Mock()
    result = Mock(action="unchanged")
    monkeypatch.setattr(
        "bulario_service.hardening.publish_candidate",
        Mock(return_value=result),
    )

    with pytest.raises(
        HardeningCheckError,
        match="was not blocked",
    ):
        _expect_publication_conflict(
            session,
            candidate=candidate(),
            expected_field="source_fingerprint",
        )

    session.rollback.assert_called_once()


class ScalarListResult:
    def __init__(self, values):
        self._values = values

    def __iter__(self):
        return iter(self._values)


class CountResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


def test_find_latest_published_current_document() -> None:
    version = BularioDocumentVersion(
        id=20,
        product_id=10,
        source_document_id=2,
        is_current=True,
        source_fingerprint="a" * 64,
    )
    product = BularioProduct(
        id=10,
        source_product_id=1,
        product_name="Produto",
    )

    session = Mock()
    session.scalars.return_value = ScalarListResult([version])
    session.get.return_value = product
    session.execute.return_value = CountResult(1)

    assert find_latest_published_current_document_id(session) == 2


def test_find_latest_published_current_document_requires_ready_row() -> None:
    version = BularioDocumentVersion(
        id=20,
        product_id=10,
        source_document_id=2,
        is_current=True,
        source_fingerprint="a" * 64,
    )
    product = BularioProduct(
        id=10,
        source_product_id=1,
        product_name="Produto",
    )

    session = Mock()
    session.scalars.return_value = ScalarListResult([version])
    session.get.return_value = product
    session.execute.return_value = CountResult(0)

    with pytest.raises(
        HardeningCheckError,
        match="no published current operational document",
    ):
        find_latest_published_current_document_id(session)
