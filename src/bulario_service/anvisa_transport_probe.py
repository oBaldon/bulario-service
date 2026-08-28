import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from playwright.sync_api import BrowserContext, Page, sync_playwright

from bulario_service.anvisa import DEFAULT_BASE_URL


BULARIO_URL = f"{DEFAULT_BASE_URL}/#/bulario/"
DISCOVERY_PATH = "/api/consulta/bulario"
DEFAULT_PROFILE_DIR = Path(".playwright/anvisa-profile-google-chrome")
DEFAULT_BROWSER_CHANNEL = "chrome"
DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Authorization": "Guest",
}


@dataclass(frozen=True)
class ProbeResult:
    transport: str
    status_code: int | None
    ok: bool
    detail: str


def build_discovery_params(
    *,
    period_start: str,
    period_end: str,
    page_size: int = 1,
) -> dict[str, str | int]:
    if page_size < 1:
        raise ValueError("page_size must be greater than or equal to 1")

    return {
        "column": "",
        "count": page_size,
        "filter[periodoPublicacaoFinal]": period_end,
        "filter[periodoPublicacaoInicial]": period_start,
        "order": "asc",
        "page": 1,
    }


def _describe_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return f"payload_type={type(payload).__name__}"

    total = payload.get("totalElements")
    count = payload.get("numberOfElements")
    return f"totalElements={total} numberOfElements={count}"


def probe_page_fetch(
    page: Page,
    *,
    params: dict[str, str | int],
) -> ProbeResult:
    query = urlencode(params)
    result = page.evaluate(
        """async ({path, query, headers}) => {
            const response = await fetch(`${path}?${query}`, {
                method: "GET",
                headers,
                credentials: "include",
            });

            let payload = null;
            try {
                payload = await response.json();
            } catch (_) {
                payload = null;
            }

            return {
                status: response.status,
                payload,
            };
        }""",
        {
            "path": DISCOVERY_PATH,
            "query": query,
            "headers": DEFAULT_HEADERS,
        },
    )

    status = int(result["status"])
    payload = result.get("payload")
    return ProbeResult(
        transport="page.fetch",
        status_code=status,
        ok=status == 200,
        detail=_describe_payload(payload),
    )


def probe_context_request(
    context: BrowserContext,
    *,
    params: dict[str, str | int],
) -> ProbeResult:
    response = context.request.get(
        f"{DEFAULT_BASE_URL}{DISCOVERY_PATH}",
        params=params,
        headers=DEFAULT_HEADERS,
    )
    status = response.status

    payload: Any = None
    try:
        payload = response.json()
    except Exception:
        pass

    return ProbeResult(
        transport="context.request",
        status_code=status,
        ok=status == 200,
        detail=_describe_payload(payload),
    )


def browser_cookies_to_httpx(
    cookies: list[dict[str, Any]],
) -> httpx.Cookies:
    jar = httpx.Cookies()
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            continue

        domain = cookie.get("domain")
        path = cookie.get("path") or "/"
        jar.set(
            name,
            value,
            domain=domain if isinstance(domain, str) else None,
            path=path,
        )
    return jar


def probe_httpx_from_browser_session(
    context: BrowserContext,
    page: Page,
    *,
    params: dict[str, str | int],
) -> ProbeResult:
    cookies = browser_cookies_to_httpx(context.cookies())

    user_agent = page.evaluate("() => navigator.userAgent")
    headers = {
        **DEFAULT_HEADERS,
        "Referer": f"{DEFAULT_BASE_URL}/",
        "User-Agent": str(user_agent),
    }

    try:
        with httpx.Client(
            base_url=DEFAULT_BASE_URL,
            cookies=cookies,
            headers=headers,
            timeout=20.0,
            follow_redirects=True,
        ) as client:
            response = client.get(DISCOVERY_PATH, params=params)
    except httpx.HTTPError as exc:
        return ProbeResult(
            transport="httpx.browser_session",
            status_code=None,
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
        )

    payload: Any = None
    try:
        payload = response.json()
    except ValueError:
        pass

    return ProbeResult(
        transport="httpx.browser_session",
        status_code=response.status_code,
        ok=response.status_code == 200,
        detail=_describe_payload(payload),
    )


def run_probe(
    *,
    period_start: str,
    period_end: str,
    profile_dir: Path,
    headless: bool,
    page_size: int = 1,
    browser_channel: str | None = DEFAULT_BROWSER_CHANNEL,
) -> list[ProbeResult]:
    params = build_discovery_params(
        period_start=period_start,
        period_end=period_end,
        page_size=page_size,
    )

    profile_dir = profile_dir.resolve()
    profile_dir.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "headless": headless,
        }
        if browser_channel:
            launch_kwargs["channel"] = browser_channel

        context = playwright.chromium.launch_persistent_context(
            **launch_kwargs,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()

            navigation = page.goto(
                BULARIO_URL,
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            navigation_status = navigation.status if navigation else None
            print(
                "browser.navigation "
                f"status={navigation_status} "
                f"url={page.url}"
            )

            results: list[ProbeResult] = []

            try:
                results.append(probe_page_fetch(page, params=params))
            except Exception as exc:
                results.append(
                    ProbeResult(
                        transport="page.fetch",
                        status_code=None,
                        ok=False,
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )

            try:
                results.append(probe_context_request(context, params=params))
            except Exception as exc:
                results.append(
                    ProbeResult(
                        transport="context.request",
                        status_code=None,
                        ok=False,
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )

            results.append(
                probe_httpx_from_browser_session(
                    context,
                    page,
                    params=params,
                )
            )
            return results
        finally:
            try:
                context.close()
            except Exception:
                pass


def _print_results(results: list[ProbeResult]) -> None:
    print()
    print("ANVISA transport probe")
    print("-" * 72)
    for result in results:
        status = result.status_code if result.status_code is not None else "-"
        outcome = "OK" if result.ok else "FAIL"
        print(
            f"{result.transport:<24} "
            f"status={status!s:<4} "
            f"{outcome:<4} "
            f"{result.detail}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compara os transportes browser fetch, Playwright APIRequestContext "
            "e httpx com a sessão obtida pelo navegador."
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
        "--page-size",
        type=int,
        default=1,
    )
    parser.add_argument(
        "--browser-channel",
        default=DEFAULT_BROWSER_CHANNEL,
        help=(
            "Canal do navegador Playwright (default: chrome). "
            "Use chromium para o build empacotado pelo Playwright."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--headed",
        action="store_true",
        help="Executa Chromium visível.",
    )
    mode.add_argument(
        "--headless",
        action="store_true",
        help="Executa Chromium em background (default).",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    try:
        results = run_probe(
            period_start=args.period_start,
            period_end=args.period_end,
            profile_dir=args.profile_dir,
            headless=not args.headed,
            page_size=args.page_size,
            browser_channel=(
                None if args.browser_channel == "chromium" else args.browser_channel
            ),
        )
    except Exception as exc:
        print(
            f"ANVISA transport probe failed before comparison: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    _print_results(results)

    if not any(result.ok for result in results):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
