import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import BrowserContext, Page, Request, Response, sync_playwright

from bulario_service.anvisa import DEFAULT_BASE_URL
from bulario_service.anvisa_transport_probe import (
    DEFAULT_PROFILE_DIR,
    DEFAULT_BROWSER_CHANNEL,
    DEFAULT_HEADERS,
    ProbeResult,
    browser_cookies_to_httpx,
)


BULARIO_URL = f"{DEFAULT_BASE_URL}/#/bulario/"
TARGET_PREFIXES = (
    "/api/consulta/bulario",
    "/api/consulta/medicamentos/arquivo/bula/parecer/",
)

SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
}


@dataclass(frozen=True)
class ObservedRequest:
    method: str
    url: str
    path: str
    status_code: int
    request_headers: dict[str, str]
    response_headers: dict[str, str]


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADERS:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def _is_target(url: str) -> bool:
    path = urlsplit(url).path
    return any(path.startswith(prefix) for prefix in TARGET_PREFIXES)


def observe_target_request(
    page: Page,
    *,
    timeout_seconds: float,
) -> ObservedRequest | None:
    captured: list[ObservedRequest] = []

    def on_response(response: Response) -> None:
        if not _is_target(response.url):
            return
        if response.status != 200:
            return

        request = response.request
        try:
            request_headers = request.all_headers()
        except Exception:
            request_headers = {}
        try:
            response_headers = response.all_headers()
        except Exception:
            response_headers = {}

        captured.append(
            ObservedRequest(
                method=request.method,
                url=response.url,
                path=urlsplit(response.url).path,
                status_code=response.status,
                request_headers=_redact_headers(request_headers),
                response_headers=_redact_headers(response_headers),
            )
        )

    page.on("response", on_response)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if captured:
            return captured[0]
        page.wait_for_timeout(250)

    return None


def _probe_page_exact(page: Page, observed: ObservedRequest) -> ProbeResult:
    result = page.evaluate(
        """async ({url}) => {
            const response = await fetch(url, {
                method: "GET",
                credentials: "include",
            });
            let payload = null;
            try {
                payload = await response.json();
            } catch (_) {}
            return {
                status: response.status,
                payloadType: payload === null ? "null" : typeof payload,
            };
        }""",
        {"url": observed.url},
    )

    status = int(result["status"])
    return ProbeResult(
        transport="page.fetch.exact",
        status_code=status,
        ok=status == 200,
        detail=f"payload_type={result.get('payloadType')}",
    )


def _probe_context_exact(
    context: BrowserContext,
    observed: ObservedRequest,
) -> ProbeResult:
    response = context.request.get(observed.url)
    return ProbeResult(
        transport="context.request.exact",
        status_code=response.status,
        ok=response.status == 200,
        detail="replay of observed URL",
    )


def _probe_httpx_exact(
    context: BrowserContext,
    page: Page,
    observed: ObservedRequest,
) -> ProbeResult:
    cookies = browser_cookies_to_httpx(context.cookies())
    user_agent = page.evaluate("() => navigator.userAgent")

    headers = {
        "Accept": observed.request_headers.get(
            "accept",
            DEFAULT_HEADERS["Accept"],
        ),
        "Authorization": "Guest",
        "Referer": page.url,
        "User-Agent": str(user_agent),
    }

    try:
        with httpx.Client(
            cookies=cookies,
            headers=headers,
            timeout=20.0,
            follow_redirects=True,
        ) as client:
            response = client.get(observed.url)
    except httpx.HTTPError as exc:
        return ProbeResult(
            transport="httpx.observed_session",
            status_code=None,
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
        )

    return ProbeResult(
        transport="httpx.observed_session",
        status_code=response.status_code,
        ok=response.status_code == 200,
        detail="replay of observed URL",
    )


def run_observer(
    *,
    profile_dir: Path,
    timeout_seconds: float,
    headed: bool,
    browser_channel: str | None = DEFAULT_BROWSER_CHANNEL,
) -> tuple[ObservedRequest | None, list[ProbeResult]]:
    profile_dir = profile_dir.resolve()
    profile_dir.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "headless": not headed,
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

            if headed:
                print()
                print(
                    "Faça uma busca normal no Bulário ou abra o detalhe de um produto. "
                    "O observador aguardará a primeira resposta 200 relevante."
                )

            observed = observe_target_request(
                page,
                timeout_seconds=timeout_seconds,
            )

            if observed is None:
                return None, []

            print()
            print("Observed SPA request")
            print("-" * 72)
            print(f"method={observed.method}")
            print(f"status={observed.status_code}")
            print(f"path={observed.path}")
            print("request_headers=")
            print(json.dumps(observed.request_headers, ensure_ascii=False, indent=2))
            print("response_headers=")
            print(json.dumps(observed.response_headers, ensure_ascii=False, indent=2))

            probes: list[ProbeResult] = []
            for probe in (
                lambda: _probe_page_exact(page, observed),
                lambda: _probe_context_exact(context, observed),
                lambda: _probe_httpx_exact(context, page, observed),
            ):
                try:
                    probes.append(probe())
                except Exception as exc:
                    probes.append(
                        ProbeResult(
                            transport="probe.error",
                            status_code=None,
                            ok=False,
                            detail=f"{type(exc).__name__}: {exc}",
                        )
                    )

            return observed, probes
        finally:
            try:
                context.close()
            except Exception:
                pass


def _print_probe_results(results: list[ProbeResult]) -> None:
    if not results:
        return

    print()
    print("Replay comparison")
    print("-" * 72)
    for result in results:
        status = result.status_code if result.status_code is not None else "-"
        outcome = "OK" if result.ok else "FAIL"
        print(
            f"{result.transport:<28} "
            f"status={status!s:<4} "
            f"{outcome:<4} "
            f"{result.detail}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Observa uma chamada 200 feita pela SPA do Bulário e compara "
            "replays pela página, Playwright request e httpx."
        )
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Executa com navegador visível para permitir interação manual.",
    )
    parser.add_argument(
        "--browser-channel",
        default=DEFAULT_BROWSER_CHANNEL,
        help=(
            "Canal do navegador Playwright (default: chrome). "
            "Use chromium para o build empacotado pelo Playwright."
        ),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    try:
        observed, probes = run_observer(
            profile_dir=args.profile_dir,
            timeout_seconds=args.timeout_seconds,
            headed=args.headed,
            browser_channel=(
                None if args.browser_channel == "chromium" else args.browser_channel
            ),
        )
    except Exception as exc:
        print(
            f"ANVISA observer failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    if observed is None:
        print(
            "Nenhuma resposta 200 relevante foi observada dentro do tempo limite.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    _print_probe_results(probes)


if __name__ == "__main__":
    main()
