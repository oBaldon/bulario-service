import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from bulario_service.document_storage import StoredBulaDocument
from bulario_service.document_text import (
    DocumentTextExtractionError,
    PdfTextExtractor,
    normalize_extracted_text,
)


def stored() -> StoredBulaDocument:
    return StoredBulaDocument(
        source_product_id=1174609,
        source_document_id=35480554,
        kind="patient",
        storage_key="bulas/1174609/35480554/patient.pdf",
        sha256="a" * 64,
        size_bytes=100,
    )


def test_normalize_extracted_text_normalizes_line_endings_and_whitespace() -> None:
    raw = "Título  \r\n\r\n\r\n\r\nLinha com espaço   \x00\rFim\n"
    normalized = normalize_extracted_text(raw)

    assert normalized == "Título\n\n\nLinha com espaço\nFim"


def test_normalize_extracted_text_applies_unicode_nfkc() -> None:
    assert normalize_extracted_text("ＡＢＣ") == "ABC"


def test_extract_returns_traceable_text_artifact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "patient.pdf"
    pdf.write_bytes(b"%PDF-test")

    monkeypatch.setattr(
        "bulario_service.document_text.shutil.which",
        lambda executable: "/usr/bin/pdftotext",
    )
    completed = Mock(
        returncode=0,
        stdout="Bula do paciente\n\nConteúdo".encode("utf-8"),
        stderr=b"",
    )
    monkeypatch.setattr(
        "bulario_service.document_text.subprocess.run",
        lambda *args, **kwargs: completed,
    )

    result = PdfTextExtractor().extract(
        pdf_path=pdf,
        stored_document=stored(),
    )

    assert result.source_product_id == 1174609
    assert result.source_document_id == 35480554
    assert result.kind == "patient"
    assert result.document_storage_key.endswith("patient.pdf")
    assert result.document_sha256 == "a" * 64
    assert result.text == "Bula do paciente\n\nConteúdo"
    assert result.text_sha256 == hashlib.sha256(
        result.text.encode("utf-8")
    ).hexdigest()
    assert result.character_count == len(result.text)


def test_extract_rejects_missing_pdf(tmp_path: Path) -> None:
    with pytest.raises(DocumentTextExtractionError, match="PDF not found"):
        PdfTextExtractor().extract(
            pdf_path=tmp_path / "missing.pdf",
            stored_document=stored(),
        )


def test_extract_rejects_missing_pdftotext(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "patient.pdf"
    pdf.write_bytes(b"%PDF-test")
    monkeypatch.setattr(
        "bulario_service.document_text.shutil.which",
        lambda executable: None,
    )

    with pytest.raises(
        DocumentTextExtractionError,
        match="executable not found",
    ):
        PdfTextExtractor().extract(
            pdf_path=pdf,
            stored_document=stored(),
        )


def test_extract_rejects_empty_normalized_text(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "patient.pdf"
    pdf.write_bytes(b"%PDF-test")
    monkeypatch.setattr(
        "bulario_service.document_text.shutil.which",
        lambda executable: "/usr/bin/pdftotext",
    )
    monkeypatch.setattr(
        "bulario_service.document_text.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=0,
            stdout=b" \n\n ",
            stderr=b"",
        ),
    )

    with pytest.raises(
        DocumentTextExtractionError,
        match="empty normalized text",
    ):
        PdfTextExtractor().extract(
            pdf_path=pdf,
            stored_document=stored(),
        )


def test_extract_does_not_include_source_token_in_artifact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "patient.pdf"
    pdf.write_bytes(b"%PDF-test")
    monkeypatch.setattr(
        "bulario_service.document_text.shutil.which",
        lambda executable: "/usr/bin/pdftotext",
    )
    monkeypatch.setattr(
        "bulario_service.document_text.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=0,
            stdout=b"valid text",
            stderr=b"",
        ),
    )

    result = PdfTextExtractor().extract(
        pdf_path=pdf,
        stored_document=stored(),
    )

    assert not hasattr(result, "token")
