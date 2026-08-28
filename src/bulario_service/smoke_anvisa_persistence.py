import argparse
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from bulario_service.anvisa import (
    AnvisaBularioConnector,
    AnvisaSourceError,
)
from bulario_service.anvisa_documents import AnvisaDocumentDownloader
from bulario_service.anvisa_session import (
    AnvisaAuthenticatedHttpClient,
    AnvisaBrowserSessionBootstrap,
)
from bulario_service.anvisa_transport_probe import DEFAULT_PROFILE_DIR
from bulario_service.config import load_settings
from bulario_service.db import create_database_engine
from bulario_service.document_storage import (
    DocumentStorageError,
    LocalDocumentStorage,
)
from bulario_service.operational_persistence import (
    OperationalPersistenceError,
    persist_operational_version,
)


DEFAULT_STORAGE_ROOT = Path("storage")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Valida discovery + detalhe + PDFs + storage + persistência "
            "operacional no schema bulario."
        )
    )
    parser.add_argument("--period-start", required=True)
    parser.add_argument("--period-end", required=True)
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=DEFAULT_STORAGE_ROOT,
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Executa o bootstrap com Google Chrome visível.",
    )
    parser.add_argument("--page-size", type=int, default=1)
    return parser


def run_smoke(
    *,
    period_start: str,
    period_end: str,
    profile_dir: Path,
    storage_root: Path,
    headed: bool,
    page_size: int,
) -> int:
    engine = None
    try:
        settings = load_settings()
        engine = create_database_engine(settings)

        bootstrap = AnvisaBrowserSessionBootstrap(
            profile_dir=profile_dir,
            headless=not headed,
        )
        session_state = bootstrap.bootstrap()
        print("Browser session bootstrap: OK")

        storage = LocalDocumentStorage(storage_root)

        with AnvisaAuthenticatedHttpClient(session_state) as authenticated:
            connector = AnvisaBularioConnector(
                client=authenticated.client,
            )
            discovery = connector.discover_page(
                page=1,
                page_size=page_size,
                period_start=period_start,
                period_end=period_end,
            )
            if not discovery.items:
                print("Nenhum produto encontrado no período.")
                return 0

            product = discovery.items[0]
            detail = connector.get_product_detail(
                product.source_product_id
            )
            current_versions = [
                candidate
                for candidate in detail.versions
                if candidate.current
            ]
            if len(current_versions) != 1:
                raise RuntimeError(
                    "expected exactly one current document version"
                )

            version = current_versions[0]
            downloader = AnvisaDocumentDownloader(
                authenticated.client,
            )

            stored_documents = []
            if version.patient_token:
                stored_documents.append(
                    storage.store(
                        source_product_id=product.source_product_id,
                        document=downloader.download(
                            source_document_id=version.source_document_id,
                            kind="patient",
                            token=version.patient_token,
                        ),
                    )
                )

            if version.professional_token:
                stored_documents.append(
                    storage.store(
                        source_product_id=product.source_product_id,
                        document=downloader.download(
                            source_document_id=version.source_document_id,
                            kind="professional",
                            token=version.professional_token,
                        ),
                    )
                )

        with Session(engine) as db_session:
            persisted = persist_operational_version(
                db_session,
                product=product,
                version=version,
                stored_documents=stored_documents,
            )
            db_session.commit()

            print(
                "Operational persistence: OK "
                f"product_id={persisted.product.id} "
                f"version_id={persisted.version.id} "
                f"source_product_id={persisted.product.source_product_id} "
                f"source_document_id={persisted.version.source_document_id} "
                f"artifacts={len(persisted.artifacts)}"
            )
            for artifact in persisted.artifacts:
                print(
                    "Operational artifact "
                    f"kind={artifact.kind} "
                    f"storage_key={artifact.storage_key} "
                    f"sha256={artifact.sha256}"
                )

        return 0

    except (
        AnvisaSourceError,
        DocumentStorageError,
        OperationalPersistenceError,
        RuntimeError,
    ) as exc:
        print(
            f"ANVISA operational persistence smoke failed: {exc}",
            file=sys.stderr,
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(
        run_smoke(
            period_start=args.period_start,
            period_end=args.period_end,
            profile_dir=args.profile_dir,
            storage_root=args.storage_root,
            headed=args.headed,
            page_size=args.page_size,
        )
    )


if __name__ == "__main__":
    main()
