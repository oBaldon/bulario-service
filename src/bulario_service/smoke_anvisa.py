import argparse
import sys
from datetime import UTC, datetime, timedelta

from bulario_service.anvisa import AnvisaBularioConnector, AnvisaSourceError


def _format_date(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT00:00:00.000Z")


def _default_period() -> tuple[str, str]:
    now = datetime.now(UTC)
    return _format_date(now - timedelta(days=2)), _format_date(now + timedelta(days=1))


def build_parser() -> argparse.ArgumentParser:
    default_start, default_end = _default_period()

    parser = argparse.ArgumentParser(
        description=(
            "Executa um smoke test controlado contra o Bulário da ANVISA. "
            "Faz uma descoberta pequena e consulta o detalhe do primeiro produto."
        )
    )
    parser.add_argument("--period-start", default=default_start)
    parser.add_argument("--period-end", default=default_end)
    parser.add_argument("--page-size", type=int, default=1)
    return parser


def run_smoke(*, period_start: str, period_end: str, page_size: int) -> int:
    try:
        with AnvisaBularioConnector() as connector:
            discovery = connector.discover_page(
                page=1,
                page_size=page_size,
                period_start=period_start,
                period_end=period_end,
            )

            print("ANVISA discovery: OK")
            print(f"total_elements={discovery.total_elements}")
            print(f"page_size={discovery.page_size}")
            print(f"returned_items={len(discovery.items)}")

            if not discovery.items:
                print(
                    "Nenhum produto encontrado no período informado; "
                    "o endpoint respondeu corretamente, mas o detalhe não foi testado."
                )
                return 0

            product = discovery.items[0]
            print(f"source_product_id={product.source_product_id}")
            print(f"product_name={product.product_name}")

            detail = connector.get_product_detail(product.source_product_id)
            current_versions = [v for v in detail.versions if v.current]

            print("ANVISA product detail: OK")
            print(f"versions={len(detail.versions)}")
            print(f"current_versions={len(current_versions)}")
            if current_versions:
                print(
                    "current_source_document_id="
                    f"{current_versions[0].source_document_id}"
                )
            return 0
    except AnvisaSourceError as exc:
        print(f"ANVISA smoke test failed: {exc}", file=sys.stderr)
        return 2


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(
        run_smoke(
            period_start=args.period_start,
            period_end=args.period_end,
            page_size=args.page_size,
        )
    )


if __name__ == "__main__":
    main()
