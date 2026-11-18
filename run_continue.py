"""Send one 'Continue' message in the most recent Claude conversation."""
import argparse
import base64
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

STATE_PATH = Path("session_state.json")

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-now", action="store_true",
                        help="Send immediately (used for a manual GitHub Actions test).")
    parser.add_argument("--scheduled", action="store_true",
                        help="For cron runs: send if target time has just passed, rather than waiting until tomorrow.")
    parser.add_argument("--time", default=os.getenv("CLAUDE_SEND_TIME") or "03:50")
    parser.add_argument("--timezone", default=os.getenv("CLAUDE_TIMEZONE") or "Europe/Moscow")
    parser.add_argument("--max-wait-minutes", type=int, default=24 * 60)
    return parser.parse_args()

def load_state():
    encoded = os.getenv("CLAUDE_SESSION_B64")
    if encoded:
        try:
            STATE_PATH.write_bytes(base64.b64decode(encoded, validate=True))
        except Exception as exc:
            raise RuntimeError("CLAUDE_SESSION_B64 отсутствует или имеет неверный формат.") from exc
    if not STATE_PATH.exists():
        raise RuntimeError("Нет session_state.json. Создайте сессию и добавьте CLAUDE_SESSION_B64 в GitHub Secrets.")
    return str(STATE_PATH)

def wait_until(target_time: str, timezone_name: str, max_wait_minutes: int, scheduled: bool = False):
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"Неизвестная временная зона: {timezone_name}") from exc
    try:
        hour, minute = map(int, target_time.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("CLAUDE_SEND_TIME должен иметь формат HH:MM, например 03:50.") from exc

    now = datetime.now(tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        # A cron job can start a few minutes late. For scheduled runs, accept
        # the same-day target within a 20-minute grace window.
        if scheduled and (now - target).total_seconds() <= 20 * 60:
            print(f"Cron запустился после целевой минуты; отправляем в пределах окна допуска ({now.isoformat()}).", flush=True)
            return
        target += timedelta(days=1)
    deadline = time.monotonic() + max_wait_minutes * 60
    print(f"Текущее время: {now.isoformat()}; цель: {target.isoformat()}", flush=True)
    while datetime.now(tz) < target:
        if time.monotonic() > deadline:
            raise TimeoutError("Превышено максимальное время ожидания.")
        remaining = (target - datetime.now(tz)).total_seconds()
        time.sleep(min(max(remaining, 1), 30))
    print("Целевое время наступило.", flush=True)

def send_message(state_file: str):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=state_file)
        page = context.new_page()
        try:
            page.goto("https://claude.ai", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)

            if "login" in page.url.lower() or page.get_by_text("Sign in", exact=False).count() > 0:
                raise RuntimeError("Похоже, авторизация истекла. Обновите CLAUDE_SESSION_B64.")

            # The first conversation link in the sidebar is generally the latest one.
            links = page.locator('a[href*="/chat/"]')
            if links.count() == 0:
                # Alternate accessible representation used by some UI versions.
                links = page.locator('a[href*="/conversation/"]')
            if links.count() == 0:
                raise RuntimeError("Не найден диалог в боковой панели. Убедитесь, что история Claude содержит нужный чат.")
            links.first.click(timeout=15000)
            page.wait_for_timeout(1800)

            # Claude's composer is exposed as a textbox/contenteditable in the UI.
            composer = page.locator('[contenteditable="true"]').last
            if composer.count() == 0:
                composer = page.get_by_role("textbox").last
            composer.wait_for(state="visible", timeout=20000)
            composer.fill("Continue")

            send_button = page.get_by_role("button", name="Send", exact=False)
            if send_button.count() == 0:
                send_button = page.locator('button[aria-label*="Send" i]')
            if send_button.count() > 0 and send_button.last.is_enabled():
                send_button.last.click()
            else:
                composer.press("Enter")

            # Wait for the UI to accept the message; don't retry automatically (avoids duplicates).
            page.wait_for_timeout(3000)
            print("Отправка инициирована. Проверьте диалог Claude.", flush=True)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError("Интерфейс Claude не ответил вовремя; проверьте авторизацию и актуальность локаторов.") from exc
        finally:
            context.close()
            browser.close()

def main():
    args = parse_args()
    state_file = load_state()
    if not args.send_now:
        wait_until(args.time, args.timezone, args.max_wait_minutes, scheduled=args.scheduled)
    send_message(state_file)
    try:
        STATE_PATH.unlink(missing_ok=True)
    except OSError:
        pass

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
