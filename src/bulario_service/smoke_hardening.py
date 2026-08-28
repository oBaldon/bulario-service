import sys

from sqlalchemy.orm import Session

from bulario_service.config import load_settings
from bulario_service.db import create_database_engine
from bulario_service.hardening import (
    HardeningCheckError,
    find_latest_published_current_document_id,
    run_hardening_checks,
)


def run_smoke() -> int:
    engine = None
    try:
        engine = create_database_engine(load_settings())

        with Session(engine) as session:
            source_document_id = (
                find_latest_published_current_document_id(session)
            )
            report = run_hardening_checks(
                session,
                source_document_id=source_document_id,
            )

        print(
            "Hardening checks: OK "
            f"source_record_id={report.source_record_id} "
            f"source_document_id={report.source_document_id}"
        )
        print(
            "CHECK public_rerun_unchanged="
            f"{str(report.public_rerun_unchanged).lower()}"
        )
        print(
            "CHECK fingerprint_conflict_blocked="
            f"{str(report.fingerprint_conflict_blocked).lower()}"
        )
        print(
            "CHECK pdf_hash_conflict_blocked="
            f"{str(report.pdf_hash_conflict_blocked).lower()}"
        )
        print(
            "CHECK text_conflict_blocked="
            f"{str(report.text_conflict_blocked).lower()}"
        )
        print(
            "CHECK operational_fingerprint_conflict_blocked="
            f"{str(report.operational_fingerprint_conflict_blocked).lower()}"
        )
        print("hardening_committed_mutations=0")
        return 0

    except (HardeningCheckError, RuntimeError, ValueError) as exc:
        print(f"Hardening checks failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


def main() -> None:
    raise SystemExit(run_smoke())


if __name__ == "__main__":
    main()
