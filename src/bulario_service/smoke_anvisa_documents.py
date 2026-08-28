import argparse
import sys
from pathlib import Path

from bulario_service.anvisa import (
    AnvisaBularioConnector,
    AnvisaSourceError,
    RequestTrace,
)
from bulario_service.anvisa_documents import (
    AnvisaDocumentDownloader,
    DocumentDownloadTrace,
)
from bulario_service.anvisa_session import (
    AnvisaAuthenticatedHttpClient,
    AnvisaBrowserSessionBootstrap,
)
from bulario_service.anvisa_transport_probe import DEFAULT_PROFILE_DIR


def print_request_trace(trace: RequestTrace) -> None:
    status = trace.status_code if trace.status_code is not None else "-"
    page = trace.page if trace.page is not None else "-"
    print(
        "ANVISA HTTP "
        f"path={trace.path} "
        f"page={page} "
        f"attempt={trace.attempt} "
        f"status={status} "
        f"elapsed={trace.elapsed_seconds:.2f}s "
        f"outcome={trace.outcome}"
    )


def print_document_trace(trace: DocumentDownloadTrace) -> None:
    status = trace.status_code if trace.status_code is not None else "-"
    size = trace.size_bytes if trace.size_bytes is not None else "-"
    print(
        "ANVISA PDF "
        f"type={trace.kind} "
        f"source_document_id={trace.source_document_id} "
        f"attempt={trace.attempt} "
        f"status={status} "
        f"bytes={size} "
        f"elapsed={trace.elapsed_seconds:.2f}s "
        f"outcome={trace.outcome}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Valida discovery + detalhe + PDFs da versão vigente usando "
            "httpx depois que o browser já foi fechado."
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
        "--headed",
        action="store_true",
        help="Executa o bootstrap com Google Chrome visível.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=1,
    )
    return parser


def run_smoke(
    *,
    period_start: str,
    period_end: str,
    profile_dir: Path,
    headed: bool,
    page_size: int,
) -> int:
    try:
        print("Bootstrapping ANVISA session with Google Chrome...")
        bootstrap = AnvisaBrowserSessionBootstrap(
            profile_dir=profile_dir,
            headless=not headed,
        )
        session_state = bootstrap.bootstrap()
        print("Browser session bootstrap: OK")
        print("Browser has been closed. Starting httpx validation...")

        with AnvisaAuthenticatedHttpClient(session_state) as authenticated:
            connector = AnvisaBularioConnector(
                client=authenticated.client,
                max_attempts=3,
                retry_backoff_seconds=(2.0, 5.0),
                trace_sink=print_request_trace,
            )

            discovery = connector.discover_page(
                page=1,
                page_size=page_size,
                period_start=period_start,
                period_end=period_end,
            )
            print("httpx discovery after browser close: OK")
            print(f"total_elements={discovery.total_elements}")
            print(f"returned_items={len(discovery.items)}")

            if not discovery.items:
                print(
                    "Nenhum produto encontrado no período; "
                    "detalhe e PDFs não foram testados."
                )
                return 0

            product = discovery.items[0]
            print(f"source_product_id={product.source_product_id}")
            print(f"product_name={product.product_name}")

            detail = connector.get_product_detail(
                product.source_product_id
            )
            current_versions = [
                version
                for version in detail.versions
                if version.current
            ]
            if len(current_versions) != 1:
                raise RuntimeError(
                    "expected exactly one current document version"
                )

            current = current_versions[0]
            print("httpx product detail after browser close: OK")
            print(f"versions={len(detail.versions)}")
            print(
                "current_source_document_id="
                f"{current.source_document_id}"
            )

            downloader = AnvisaDocumentDownloader(
                authenticated.client,
                max_attempts=3,
                retry_backoff_seconds=(2.0, 5.0),
                trace_sink=print_document_trace,
            )

            downloaded = []
            if current.patient_token:
                downloaded.append(
                    downloader.download(
                        source_document_id=current.source_document_id,
                        kind="patient",
                        token=current.patient_token,
                    )
                )
            else:
                print("Current version has no patient PDF token.")

            if current.professional_token:
                downloaded.append(
                    downloader.download(
                        source_document_id=current.source_document_id,
                        kind="professional",
                        token=current.professional_token,
                    )
                )
            else:
                print("Current version has no professional PDF token.")

            for document in downloaded:
                print(
                    "PDF validated "
                    f"type={document.kind} "
                    f"bytes={document.size_bytes} "
                    f"sha256={document.sha256}"
                )

            if not downloaded:
                print("Nenhum PDF disponível na versão vigente.")
                return 0

            print(f"validated_pdfs={len(downloaded)}")
            return 0

    except (AnvisaSourceError, RuntimeError) as exc:
        print(
            f"ANVISA document smoke failed: {exc}",
            file=sys.stderr,
        )
        return 2


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(
        run_smoke(
            period_start=args.period_start,
            period_end=args.period_end,
            profile_dir=args.profile_dir,
            headed=args.headed,
            page_size=args.page_size,
        )
    )


if __name__ == "__main__":
    main()
