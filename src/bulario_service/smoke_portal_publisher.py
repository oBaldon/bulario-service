import argparse
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
from bulario_service.publication_publisher import (
    BulaPublicationError,
    publish_candidate,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Publica transacionalmente a versão operacional vigente em "
            "public.bulas após validar BULA_CONTRACT_V1."
        )
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Confirma a escrita real. Sem esta flag, o comando executa "
            "a operação e faz rollback."
        ),
    )
    return parser


def run_smoke(*, write: bool) -> int:
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
            result = publish_candidate(
                session,
                candidate=candidate,
            )

            if write:
                session.commit()
                committed = 1
            else:
                session.rollback()
                committed = 0

        print(
            "Portal publication "
            f"action={result.action} "
            f"row_id={result.row_id} "
            f"source_record_id={result.source_record_id} "
            f"status={candidate.ingestion_status}"
        )
        print(f"public_bulas_committed={committed}")
        return 0

    except (
        BulaPublicationContractError,
        BulaPublicationError,
        RuntimeError,
    ) as exc:
        print(f"Portal publication failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(run_smoke(write=args.write))


if __name__ == "__main__":
    main()
