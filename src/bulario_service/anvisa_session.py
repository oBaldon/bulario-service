from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import sync_playwright

from bulario_service.anvisa import DEFAULT_BASE_URL
from bulario_service.anvisa_transport_probe import (
    BULARIO_URL,
    DEFAULT_BROWSER_CHANNEL,
    DEFAULT_HEADERS,
    DEFAULT_PROFILE_DIR,
    browser_cookies_to_httpx,
)


@dataclass(frozen=True)
class BrowserSessionState:
    cookies: tuple[dict[str, Any], ...]
    user_agent: str
    referer: str


class AnvisaBrowserSessionBootstrap:
    def __init__(
        self,
        *,
        profile_dir: Path = DEFAULT_PROFILE_DIR,
        browser_channel: str | None = DEFAULT_BROWSER_CHANNEL,
        headless: bool = False,
        navigation_timeout_ms: int = 60_000,
    ) -> None:
        self._profile_dir = profile_dir
        self._browser_channel = browser_channel
        self._headless = headless
        self._navigation_timeout_ms = navigation_timeout_ms

    def bootstrap(self) -> BrowserSessionState:
        profile_dir = self._profile_dir.resolve()
        profile_dir.parent.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            launch_kwargs: dict[str, Any] = {
                "user_data_dir": str(profile_dir),
                "headless": self._headless,
            }
            if self._browser_channel:
                launch_kwargs["channel"] = self._browser_channel

            context = playwright.chromium.launch_persistent_context(
                **launch_kwargs,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                response = page.goto(
                    BULARIO_URL,
                    wait_until="domcontentloaded",
                    timeout=self._navigation_timeout_ms,
                )

                if response is None:
                    raise RuntimeError("ANVISA navigation returned no response")
                if response.status != 200:
                    raise RuntimeError(
                        f"ANVISA navigation returned HTTP {response.status}"
                    )

                user_agent = str(
                    page.evaluate("() => navigator.userAgent")
                )
                cookies = tuple(context.cookies())

                return BrowserSessionState(
                    cookies=cookies,
                    user_agent=user_agent,
                    referer=f"{DEFAULT_BASE_URL}/",
                )
            finally:
                try:
                    context.close()
                except Exception:
                    pass


class AnvisaAuthenticatedHttpClient:
    def __init__(
        self,
        session_state: BrowserSessionState,
        *,
        timeout_seconds: float = 60.0,
    ) -> None:
        headers = {
            **DEFAULT_HEADERS,
            "Referer": session_state.referer,
            "User-Agent": session_state.user_agent,
        }

        self._client = httpx.Client(
            base_url=DEFAULT_BASE_URL,
            cookies=browser_cookies_to_httpx(
                list(session_state.cookies)
            ),
            headers=headers,
            timeout=httpx.Timeout(
                connect=min(timeout_seconds, 10.0),
                read=timeout_seconds,
                write=timeout_seconds,
                pool=min(timeout_seconds, 10.0),
            ),
            follow_redirects=True,
        )

    @property
    def client(self) -> httpx.Client:
        return self._client

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AnvisaAuthenticatedHttpClient":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
