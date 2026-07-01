"""CDP 브라우저 연결 + playwright-stealth"""
import os
import socket

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from playwright_stealth import Stealth

_stealth = Stealth()

_PORT = int(os.environ.get("CHROME_PORT", "9222"))
_CDP_URL = f"http://localhost:{_PORT}"

# stealth가 이미 적용된 context 추적 (중복 적용 방지)
_stealth_applied_contexts: set[int] = set()


def _port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", _PORT)) == 0


def connect():
    """CDP 연결 + stealth 준비된 (pw, browser) 반환"""
    if not _port_open():
        raise RuntimeError(
            f"Chrome이 CDP 모드로 실행되어 있지 않습니다 (포트 {_PORT}). "
            "Chrome을 --remote-debugging-port=9222 옵션으로 실행하세요."
        )
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(_CDP_URL)
    except Exception as e:
        pw.stop()
        raise RuntimeError(f"CDP 연결 실패: {e}")
    return pw, browser


def _apply_stealth_to_context(ctx: BrowserContext) -> None:
    """컨텍스트 레벨에서 stealth 적용 — 이 context에서 열리는 모든 페이지에 자동 주입됨."""
    ctx_id = id(ctx)
    if ctx_id in _stealth_applied_contexts:
        return
    try:
        _stealth.apply_stealth_sync(ctx)
        _stealth_applied_contexts.add(ctx_id)
    except Exception:
        # context 레벨 적용 실패 시 무시 (페이지 레벨에서 보완)
        pass


def get_page(browser: Browser, url_fragment: str = None, navigate_to: str = None) -> Page:
    """기존 탭 재사용 또는 새 탭 생성, stealth 자동 적용"""
    # 기존 context 우선 사용 (사용자 쿠키/세션 보존)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()

    # stealth를 context 레벨에서 적용 (모든 페이지에 일괄 적용)
    _apply_stealth_to_context(ctx)

    if url_fragment:
        for p in ctx.pages:
            if url_fragment in p.url:
                return p

    if navigate_to:
        page = ctx.new_page()
    else:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # 페이지 레벨에서도 stealth 적용 (이미 열려있는 페이지 대응)
    try:
        _stealth.apply_stealth_sync(page)
    except Exception:
        pass

    if navigate_to:
        page.goto(navigate_to, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)

    return page
