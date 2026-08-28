from datetime import datetime, timezone
import hashlib
from unittest.mock import Mock

import pytest

from bulario_service.publication_contract import (
    BulaPublicationCandidate,
    PublicationDocument,
)
from bulario_service.publication_publisher import (
    BulaPublicationConflictError,
    publish_candidate,
)


def candidate() -> BulaPublicationCandidate:
    patient_text = "Texto paciente"
    professional_text = "Texto profissional"

    def doc(kind: str, text: str, pdf_sha: str) -> PublicationDocument:
        return PublicationDocument(
            kind=kind,
            storage_key=f"bulas/1174609/35480554/{kind}.pdf",
            document_sha256=pdf_sha,
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            text_content=text,
            character_count=len(text),
            extraction_method="pdftotext-layout-utf8",
            normalization_version="v1",
        )

    return BulaPublicationCandidate(
        source="ANVISA_BULARIO",
        source_record_id="anvisa:1174609:35480554",
        source_url="https://consultas.anvisa.gov.br/#/bulario/",
        source_product_id=1174609,
        source_document_id=35480554,
        source_fingerprint="f" * 64,
        ingested_at=datetime.now(timezone.utc),
        ingestion_status="ready",
        registration_number="123",
        product_name="Produto",
        company_name="Empresa",
        company_cnpj="00000000000000",
        process_number="25351",
        expedient="456",
        source_publication_date="28/08/2026",
        patient=doc("patient", patient_text, "a" * 64),
        professional=doc(
            "professional",
            professional_text,
            "b" * 64,
        ),
    )


class MappingResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


def existing_row(c: BulaPublicationCandidate):
    return {
        "id": 42,
        "medicamento": c.product_name,
        "empresa": c.company_name,
        "numero_registro": c.registration_number,
        "num_expediente": c.expedient,
        "cnpj": c.company_cnpj,
        "data_publicacao": c.source_publication_date,
        "bula_paciente": c.patient.storage_key,
        "bula_profissional": c.professional.storage_key,
        "source_record_id": c.source_record_id,
        "source_url": c.source_url,
        "source_fingerprint": c.source_fingerprint,
        "ingestion_status": "ready",
        "bula_paciente_sha256": c.patient.document_sha256,
        "bula_profissional_sha256": c.professional.document_sha256,
    }


def test_publish_inserts_complete_ready_row() -> None:
    c = candidate()
    session = Mock()
    session.execute.side_effect = [
        ScalarResult(None),
        MappingResult([]),
        ScalarResult(42),
    ]

    result = publish_candidate(
        session,
        candidate=c,
        now=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )

    assert result.action == "inserted"
    assert result.row_id == 42

    insert_call = session.execute.call_args_list[2]
    params = insert_call.args[1]
    assert params["medicamento"] == "Produto"
    assert params["bula_paciente"] == c.patient.storage_key
    assert params["bula_profissional"] == c.professional.storage_key
    assert params["ingestion_status"] == "ready"
    assert params["bula_paciente_sha256"] == "a" * 64
    assert params["bula_profissional_sha256"] == "b" * 64


def test_identical_rerun_is_unchanged() -> None:
    c = candidate()
    session = Mock()
    session.execute.side_effect = [
        ScalarResult(None),
        MappingResult([existing_row(c)]),
    ]

    result = publish_candidate(session, candidate=c)

    assert result.action == "unchanged"
    assert result.row_id == 42
    assert session.execute.call_count == 2


def test_existing_row_with_changed_fingerprint_is_conflict() -> None:
    c = candidate()
    row = existing_row(c)
    row["source_fingerprint"] = "0" * 64

    session = Mock()
    session.execute.side_effect = [
        ScalarResult(None),
        MappingResult([row]),
    ]

    with pytest.raises(
        BulaPublicationConflictError,
        match="immutable",
    ):
        publish_candidate(session, candidate=c)


def test_existing_row_with_changed_pdf_hash_is_conflict() -> None:
    c = candidate()
    row = existing_row(c)
    row["bula_paciente_sha256"] = "0" * 64

    session = Mock()
    session.execute.side_effect = [
        ScalarResult(None),
        MappingResult([row]),
    ]

    with pytest.raises(
        BulaPublicationConflictError,
        match="bula_paciente_sha256",
    ):
        publish_candidate(session, candidate=c)


def test_duplicate_source_record_rows_are_conflict() -> None:
    c = candidate()
    session = Mock()
    session.execute.side_effect = [
        ScalarResult(None),
        MappingResult([existing_row(c), existing_row(c)]),
    ]

    with pytest.raises(
        BulaPublicationConflictError,
        match="multiple public.bulas rows",
    ):
        publish_candidate(session, candidate=c)


def test_publisher_takes_transaction_advisory_lock_first() -> None:
    c = candidate()
    session = Mock()
    session.execute.side_effect = [
        ScalarResult(None),
        MappingResult([existing_row(c)]),
    ]

    publish_candidate(session, candidate=c)

    statement = str(session.execute.call_args_list[0].args[0])
    assert "pg_advisory_xact_lock" in statement
    assert "hashtextextended" in statement
