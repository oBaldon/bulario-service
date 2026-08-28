import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from bulario_service.config import load_settings
from bulario_service.db import create_database_engine
from bulario_service.models import BularioDocumentVersion
from bulario_service.publication_contract import (
    BulaPublicationContractError,
    build_publication_candidate,
)


def run_smoke() -> int:
    engine = None
    try:
        engine = create_database_engine(load_settings())

        with Session(engine) as session:
            version = session.scalar(
                select(BularioDocumentVersion)
                .where(BularioDocumentVersion.is_current.is_(True))
                .order_by(BularioDocumentVersion.id.desc())
            )
            if version is None:
                print("Nenhuma versão operacional vigente encontrada.")
                return 0

            candidate = build_publication_candidate(
                session,
                source_document_id=version.source_document_id,
            )

        print(
            "BULA_CONTRACT_V1 candidate: OK "
            f"source_record_id={candidate.source_record_id} "
            f"status={candidate.ingestion_status} "
            f"patient_pdf_sha256={candidate.patient.document_sha256} "
            f"patient_text_sha256={candidate.patient.text_sha256} "
            f"professional_pdf_sha256={candidate.professional.document_sha256} "
            f"professional_text_sha256={candidate.professional.text_sha256}"
        )
        print("public_bulas_written=0")
        return 0

    except (BulaPublicationContractError, RuntimeError) as exc:
        print(
            f"BULA_CONTRACT_V1 dry-run failed: {exc}",
            file=sys.stderr,
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


def main() -> None:
    raise SystemExit(run_smoke())


if __name__ == "__main__":
    main()
