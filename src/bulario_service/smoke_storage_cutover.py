import argparse
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from bulario_service.config import load_settings
from bulario_service.db import create_database_engine
from bulario_service.storage_cutover import (
    StorageCutoverError,
    reconcile_shared_storage,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcilia PDFs do storage legado com o acervo compartilhado "
            "configurado para o produtor."
        )
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("storage"),
        help="Storage legado/local usado antes do acervo compartilhado.",
    )
    parser.add_argument(
        "--target-root",
        type=Path,
        default=None,
        help=(
            "Destino do acervo. Quando omitido, usa BULARIO_STORAGE_ROOT."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Efetiva as cópias; sem esta flag, executa somente dry-run.",
    )
    return parser


def run_smoke(
    *,
    source_root: Path,
    target_root: Path | None,
    write: bool,
) -> int:
    engine = None
    try:
        settings = load_settings()
        engine = create_database_engine(settings)
        effective_target = target_root or settings.storage_root

        if effective_target.resolve() == source_root.resolve():
            raise StorageCutoverError(
                "source and target storage roots must be different"
            )

        with Session(engine) as session:
            report = reconcile_shared_storage(
                session,
                source_root=source_root,
                target_root=effective_target,
                write=write,
            )

        print(
            "Storage cutover: OK "
            f"source_root={source_root.resolve()} "
            f"target_root={effective_target.resolve()} "
            f"total_artifacts={report.total_artifacts} "
            f"copied={report.copied} "
            f"pending_copy={report.pending_copy} "
            f"reused={report.reused}"
        )
        print(f"storage_cutover_write={str(write).lower()}")
        return 0

    except (StorageCutoverError, RuntimeError) as exc:
        print(f"Storage cutover failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(
        run_smoke(
            source_root=args.source_root,
            target_root=args.target_root,
            write=args.write,
        )
    )


if __name__ == "__main__":
    main()
