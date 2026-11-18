"""Interactive one-time session capture. Never commit session_state.json."""
import base64
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path("session_state.json")

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://claude.ai", wait_until="domcontentloaded")
        print("Войдите в Claude в открывшемся окне. Не закрывайте его.")
        input("Когда увидите свой аккаунт Claude, нажмите Enter здесь...")
        context.storage_state(path=str(OUT))
        browser.close()

    raw = OUT.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    print("\nСессия сохранена в session_state.json.")
    print("Добавьте следующую строку как GitHub Secret CLAUDE_SESSION_B64:")
    print(encoded)
    print("\nНе публикуйте эту строку: она содержит авторизационные данные.")

if __name__ == "__main__":
    main()
