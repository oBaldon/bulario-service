from dataclasses import dataclass
import hashlib
import shutil
import subprocess
import unicodedata
from pathlib import Path

from bulario_service.document_storage import StoredBulaDocument


class DocumentTextExtractionError(RuntimeError):
    """Raised when a stored PDF cannot be converted to usable text."""


@dataclass(frozen=True)
class ExtractedBulaText:
    source_product_id: int
    source_document_id: int
    kind: str
    document_storage_key: str
    document_sha256: str
    text: str
    text_sha256: str
    character_count: int


class PdfTextExtractor:
    def __init__(self, *, executable: str = "pdftotext") -> None:
        self._executable = executable

    def extract(
        self,
        *,
        pdf_path: Path,
        stored_document: StoredBulaDocument,
    ) -> ExtractedBulaText:
        if not pdf_path.is_file():
            raise DocumentTextExtractionError(
                f"PDF not found for storage_key={stored_document.storage_key}"
            )

        executable = shutil.which(self._executable)
        if executable is None:
            raise DocumentTextExtractionError(
                f"PDF text extractor executable not found: {self._executable}"
            )

        try:
            result = subprocess.run(
                [
                    executable,
                    "-enc",
                    "UTF-8",
                    "-layout",
                    str(pdf_path),
                    "-",
                ],
                check=False,
                capture_output=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise DocumentTextExtractionError(
                "PDF text extraction timed out "
                f"storage_key={stored_document.storage_key}"
            ) from exc
        except OSError as exc:
            raise DocumentTextExtractionError(
                "PDF text extraction failed to start "
                f"storage_key={stored_document.storage_key}"
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise DocumentTextExtractionError(
                "PDF text extraction failed "
                f"storage_key={stored_document.storage_key} "
                f"exit_code={result.returncode} "
                f"error={stderr[:300]}"
            )

        raw_text = result.stdout.decode("utf-8", errors="replace")
        normalized = normalize_extracted_text(raw_text)
        if not normalized:
            raise DocumentTextExtractionError(
                "PDF produced empty normalized text "
                f"storage_key={stored_document.storage_key}"
            )

        text_sha256 = hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()

        return ExtractedBulaText(
            source_product_id=stored_document.source_product_id,
            source_document_id=stored_document.source_document_id,
            kind=stored_document.kind,
            document_storage_key=stored_document.storage_key,
            document_sha256=stored_document.sha256,
            text=normalized,
            text_sha256=text_sha256,
            character_count=len(normalized),
        )


def normalize_extracted_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")

    lines = [line.rstrip() for line in text.split("\n")]

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    normalized_lines: list[str] = []
    blank_run = 0

    for line in lines:
        if not line.strip():
            blank_run += 1
            if blank_run <= 2:
                normalized_lines.append("")
            continue

        blank_run = 0
        normalized_lines.append(line)

    return "\n".join(normalized_lines)
