import argparse
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from bulario_service.config import load_settings
from bulario_service.db import create_database_engine
from bulario_service.portal_handoff import (
    PortalHandoffError,
    validate_latest_ready_handoff,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Valida o handoff produtor -> Portal para a linha ready mais recente."
        )
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=Path("storage"),
    )
    return parser


def run_smoke(*, storage_root: Path) -> int:
    engine = None
    try:
        engine = create_database_engine(load_settings())
        with Session(engine) as session:
            report = validate_latest_ready_handoff(
                session,
                storage_root=storage_root,
            )

        print(
            "Portal handoff: OK "
            f"public_row_id={report.public_row_id} "
            f"source_record_id={report.source_record_id} "
            f"source_product_id={report.source_product_id} "
            f"source_document_id={report.source_document_id} "
            f"status={report.ingestion_status}"
        )
        print(
            "patient "
            f"storage_key={report.patient_storage_key} "
            f"sha256={report.patient_sha256}"
        )
        print(
            "professional "
            f"storage_key={report.professional_storage_key} "
            f"sha256={report.professional_sha256}"
        )
        print("producer_portal_handoff_ready=true")
        return 0
    except (PortalHandoffError, RuntimeError) as exc:
        print(f"Portal handoff failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(run_smoke(storage_root=args.storage_root))


if __name__ == "__main__":
    main()
