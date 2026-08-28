from pathlib import Path

from playwright.sync_api import sync_playwright


PROFILE_DIR = Path(".playwright/anvisa-profile").resolve()
URL = "https://consultas.anvisa.gov.br/#/bulario/"


with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
    )

    page = context.pages[0] if context.pages else context.new_page()

    print("Abrindo Bulário...")
    page.goto(
        URL,
        wait_until="domcontentloaded",
        timeout=60_000,
    )

    print("Página aberta.")
    print("URL atual:", page.url)
    print()
    input("Navegue normalmente e pressione ENTER aqui para encerrar...")

    context.close()