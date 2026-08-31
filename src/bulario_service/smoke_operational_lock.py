import argparse
import time

from bulario_service.config import load_settings
from bulario_service.db import create_database_engine
from bulario_service.operational_lock import (
    OperationalLockUnavailableError,
    operational_sync_lock,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Smoke real do PostgreSQL operational advisory lock.",
    )
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=20.0,
        help="Tempo para manter o lock após adquiri-lo.",
    )
    return parser


def run(*, hold_seconds: float) -> int:
    if hold_seconds < 0:
        raise ValueError("hold_seconds must be zero or greater")

    settings = load_settings()
    engine = create_database_engine(settings)
    try:
        try:
            with operational_sync_lock(engine, mode="lock-smoke"):
                print(
                    "operational_lock_acquired=true "
                    f"hold_seconds={hold_seconds:g}",
                    flush=True,
                )
                if hold_seconds:
                    time.sleep(hold_seconds)
                print("operational_lock_releasing=true", flush=True)
        except OperationalLockUnavailableError as exc:
            print(
                f"operational_lock_acquired=false error={exc}",
                flush=True,
            )
            return 3

        print("operational_lock_released=true", flush=True)
        return 0
    finally:
        engine.dispose()


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(run(hold_seconds=args.hold_seconds))


if __name__ == "__main__":
    main()
