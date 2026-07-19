"""저장된 브라우저 계정 선택 기반 로그인/로그아웃 헬퍼."""
from __future__ import annotations

import os
import re
import time

from config import read_env_values


NAVER_LOGIN_URL = "https://nid.naver.com/nidlogin.login"
NAVER_LOGOUT_URL = "https://nid.naver.com/nidlogin.logout"
TISTORY_LOGIN_URL = "https://www.tistory.com/auth/login"
TISTORY_LOGOUT_URL = "https://www.tistory.com/auth/logout"


def env_flag(key: str, default: bool = False) -> bool:
    values = read_env_values()
    raw = values.get(key, os.environ.get(key, "1" if default else "0"))
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def password_fallback_enabled() -> bool:
    return env_flag("LOGIN_PASSWORD_FALLBACK", False)


def select_saved_account_enabled() -> bool:
    return env_flag("LOGIN_SELECT_SAVED_ACCOUNT", True)


def logout_after_post_enabled() -> bool:
    return env_flag("LOGIN_LOGOUT_AFTER_POST", True)


def _log(on_log, message: str) -> None:
    if on_log:
        on_log(message)


def _safe_env_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.upper())


def _password(prefix: str, login_id: str) -> str:
    key = f"{prefix}_{_safe_env_id(login_id)}_PW"
    return read_env_values().get(key, os.environ.get(key, "")).strip()


def _rand(page, lo=600, hi=1300) -> None:
    import random

    page.wait_for_timeout(random.randint(lo, hi))


def _click_login_button(page, selectors: list[str], on_log=None) -> bool:
    try:
        clicked = page.evaluate("""() => {
            const candidates = [
                document.getElementById('loginBtn_row'),
                document.getElementById('loginBtn_column'),
                document.getElementById('log.login'),
                document.querySelector('button.btn_login'),
                document.querySelector('button.btn_global'),
                document.querySelector('button[type="submit"]'),
                document.querySelector('input[type="submit"]'),
            ].filter(Boolean);
            for (const el of candidates) {
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) continue;
                el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window}));
                el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window}));
                el.click();
                return el.id || el.className || el.tagName;
            }
            return '';
        }""")
        if clicked:
            _log(on_log, f"[로그인] 로그인 버튼 JS 클릭: {clicked}")
            return True
    except Exception:
        pass

    for selector in selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=1500):
                btn.click(timeout=5000)
                _log(on_log, f"[로그인] 로그인 버튼 클릭: {selector}")
                return True
        except Exception:
            continue
    try:
        return bool(page.evaluate("""() => {
            const candidates = Array.from(document.querySelectorAll('button, input[type="submit"], a'));
            const target = candidates.find(el => /로그인|Login|login/.test((el.innerText || el.value || el.textContent || '').trim()));
            if (!target) return false;
            target.click();
            return true;
        }"""))
    except Exception:
        return False


def _click_saved_account_by_text(page, account_id: str, on_log=None) -> bool:
    if not account_id:
        return False
    selectors = [
        f'[data-id="{account_id}"]',
        f'a[title="{account_id}"]',
        'a.wrap_profile',
        '.account_list li',
        '.account_list a',
        '#savedAccountList li',
        '#savedAccountList a',
        '.saved_id_wrap li',
        '[role="option"]',
        'li',
        'a',
        'button',
    ]
    for selector in selectors:
        try:
            items = page.locator(selector)
            count = min(items.count(), 80)
        except Exception:
            continue
        for i in range(count):
            item = items.nth(i)
            try:
                data_id = item.get_attribute("data-id") or ""
                title = item.get_attribute("title") or ""
                text = (item.inner_text(timeout=800) or "").strip()
                haystack = "\n".join([data_id, title, text])
                if account_id in haystack:
                    item.click(timeout=3000)
                    _log(on_log, f"[로그인] 저장 계정 선택: {account_id}")
                    _rand(page, 800, 1300)
                    return True
            except Exception:
                continue
    try:
        clicked = page.evaluate("""(accountId) => {
            const nodes = Array.from(document.querySelectorAll('a, li, button, [role="option"], [data-id]'));
            for (const el of nodes) {
                const text = [el.getAttribute('data-id') || '', el.getAttribute('title') || '', el.textContent || ''].join('\n');
                if (!text.includes(accountId)) continue;
                el.click();
                return true;
            }
            return false;
        }""", account_id)
        if clicked:
            _log(on_log, f"[로그인] 저장 계정 선택(JS): {account_id}")
            _rand(page, 800, 1300)
            return True
    except Exception:
        pass
    return False


def _fill_id_and_wait_saved_password(page, id_selector: str, pw_selector: str, account_id: str, on_log=None) -> bool:
    """ID 입력 후 Chrome 저장 비밀번호 자동완성이 채워지는지만 확인한다.

    비밀번호를 앱이 직접 입력하지 않는다. 브라우저가 저장 비밀번호를 자동으로
    채운 경우에만 True를 반환한다.
    """
    if not account_id:
        return False
    try:
        id_input = page.locator(id_selector).first
        pw_input = page.locator(pw_selector).first
        id_input.wait_for(state="visible", timeout=5000)
        id_input.click(click_count=3, timeout=3000)
        id_input.fill(account_id)
        _rand(page, 500, 900)
        pw_input.click(timeout=3000)
        for _ in range(8):
            _rand(page, 300, 600)
            try:
                if len(pw_input.input_value(timeout=800) or "") > 0:
                    _log(on_log, f"[로그인] 저장 비밀번호 자동완성 확인: {account_id}")
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def ensure_naver_login(page, blog_id: str, account_id: str = "", on_log=None) -> bool:
    """네이버 로그인 페이지에서 Chrome 저장 계정을 선택해 로그인한다."""
    if not select_saved_account_enabled():
        _log(on_log, "[네이버로그인] 저장 계정 자동 선택이 꺼져 있습니다.")
        return False

    account_id = (account_id or blog_id or "").strip()
    _log(on_log, f"[네이버로그인] 목표 계정: {account_id or '(미지정)'}")
    page.goto(NAVER_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    _rand(page, 1200, 2200)

    if "nidlogin" not in page.url and "nid.naver.com" not in page.url:
        return True

    try:
        id_input = page.locator("#id").first
        id_input.wait_for(state="visible", timeout=10000)
        id_input.click(timeout=3000)
        _rand(page, 600, 1000)
        current_id = (id_input.input_value(timeout=1000) or "").strip()
    except Exception:
        current_id = ""

    selected = bool(account_id and current_id == account_id)
    if account_id and not selected:
        selected = _click_saved_account_by_text(page, account_id, on_log=on_log)

    if not selected and account_id:
        selected = _fill_id_and_wait_saved_password(page, "#id", "#pw", account_id, on_log=on_log)

    if not selected and password_fallback_enabled() and account_id:
        pw = _password("NAVER", account_id)
        if pw:
            _log(on_log, "[네이버로그인] 저장 계정 선택 실패 — 비밀번호 폴백 사용")
            page.locator("#id").fill(account_id)
            page.locator("#pw").fill(pw)
            selected = True
        else:
            _log(on_log, f"[네이버로그인] NAVER_{_safe_env_id(account_id)}_PW 값이 없습니다.")

    if not selected and account_id:
        _log(on_log, f"[네이버로그인] 저장 계정 목록에서 '{account_id}'을 찾지 못했습니다.")
        return False

    try:
        pw_input = page.locator("#pw").first
        if pw_input.is_visible(timeout=2500):
            pw_input.click(timeout=3000)
            _rand(page, 600, 1200)
    except Exception:
        pass

    # 네이버는 계정 전환 직후 첫 시도에서 저장 비밀번호가 맞아도
    # "아이디 또는 비밀번호가 올바르지 않습니다"를 한 번 띄우는 경우가 있어
    # 같은 자동완성 값으로 최대 2회 시도한다.
    for attempt in range(2):
        _log(on_log, f"[네이버로그인] 로그인 시도 {attempt + 1}/2")
        try:
            pw_input = page.locator("#pw").first
            if pw_input.is_visible(timeout=1500):
                if account_id and not (pw_input.input_value(timeout=800) or ""):
                    _fill_id_and_wait_saved_password(page, "#id", "#pw", account_id, on_log=on_log)
                pw_input.click(timeout=3000)
                _rand(page, 400, 800)
        except Exception:
            pass
        clicked = _click_login_button(
            page,
            ["#log\\.login", "button.btn_login", "button[type='submit']", "input[type='submit']"],
            on_log=on_log,
        )
        if not clicked:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
        for _ in range(18):
            _rand(page, 700, 1100)
            if "nidlogin" not in page.url and "nid.naver.com" not in page.url:
                _log(on_log, f"[네이버로그인] 로그인 완료: {page.url}")
                return True
        try:
            body_text = page.evaluate("() => document.body.innerText || ''")
        except Exception:
            body_text = ""
        if attempt == 0:
            if re.search(r"비밀번호가 올바르지|비밀번호.*다시|아이디 또는 비밀번호", body_text):
                _log(on_log, "[네이버로그인] 1차 실패 문구 감지 — 같은 저장 계정으로 재시도")
            else:
                _log(on_log, "[네이버로그인] 1차 시도 후 로그인 페이지 유지 — 재시도")
            continue
        break
    _log(on_log, f"[네이버로그인] 로그인 미완료: {page.url}")
    return False


def ensure_tistory_login(page, blog_id: str, kakao_id: str = "", on_log=None) -> bool:
    """티스토리 카카오 로그인에서 Chrome 저장 계정을 선택한다."""
    if not select_saved_account_enabled():
        _log(on_log, "[티스토리로그인] 저장 계정 자동 선택이 꺼져 있습니다.")
        return False

    kakao_id = (kakao_id or "").strip()
    _log(on_log, f"[티스토리로그인] 목표 카카오 계정: {kakao_id or '(미지정)'}")
    page.goto(TISTORY_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    _rand(page, 1200, 2200)

    if "tistory.com" in page.url and "auth/login" not in page.url and "accounts.kakao" not in page.url:
        return True

    if "auth/login" in page.url:
        for selector in ['a.btn_login.link_kakao_id', 'a[class*="kakao"]', 'button:has-text("카카오")']:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=3000):
                    btn.click(timeout=5000)
                    _rand(page, 1800, 3000)
                    break
            except Exception:
                continue

    try:
        other = page.locator('a:has-text("다른 계정"), button:has-text("다른 계정"), a:has-text("계정 전환")').first
        if other.is_visible(timeout=2000):
            other.click(timeout=3000)
            _rand(page, 1000, 1800)
    except Exception:
        pass

    selected = _click_saved_account_by_text(page, kakao_id, on_log=on_log) if kakao_id else False

    if not selected and kakao_id:
        for id_selector in ['input[name="loginId"]', '#loginId', 'input[type="email"]', 'input[name="email"]']:
            try:
                if page.locator(id_selector).first.is_visible(timeout=1200):
                    selected = _fill_id_and_wait_saved_password(page, id_selector, 'input[type="password"]', kakao_id, on_log=on_log)
                    break
            except Exception:
                continue

    if not selected and password_fallback_enabled() and kakao_id:
        pw = _password("KAKAO", kakao_id)
        if pw:
            _log(on_log, "[티스토리로그인] 저장 계정 선택 실패 — 비밀번호 폴백 사용")
            for selector in ['input[name="loginId"]', '#loginId', 'input[type="email"]', 'input[name="email"]']:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=2000):
                        el.fill(kakao_id)
                        break
                except Exception:
                    continue
            for selector in ['input[name="password"]', '#password', 'input[type="password"]']:
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=2000):
                        el.fill(pw)
                        break
                except Exception:
                    continue
            selected = True
        else:
            _log(on_log, f"[티스토리로그인] KAKAO_{_safe_env_id(kakao_id)}_PW 값이 없습니다.")

    if not selected and kakao_id:
        _log(on_log, f"[티스토리로그인] 저장 계정 목록에서 '{kakao_id}'을 찾지 못했습니다.")
        return False

    _click_login_button(page, ["button.submit", "button[type='submit']", "input[type='submit']"], on_log=on_log)
    for _ in range(35):
        _rand(page, 700, 1100)
        if "tistory.com" in page.url and "auth/login" not in page.url and "accounts.kakao" not in page.url:
            _log(on_log, f"[티스토리로그인] 로그인 완료: {page.url}")
            return True
    _log(on_log, f"[티스토리로그인] 로그인 미완료: {page.url}")
    return False


def logout_naver(page, on_log=None) -> bool:
    try:
        page.goto(NAVER_LOGOUT_URL, wait_until="domcontentloaded", timeout=15000)
        _rand(page, 1000, 1800)
        _log(on_log, "[네이버로그아웃] 완료")
        return True
    except Exception as exc:
        _log(on_log, f"[네이버로그아웃] 실패: {exc}")
        return False


def logout_tistory(page, on_log=None) -> bool:
    try:
        page.goto(TISTORY_LOGOUT_URL, wait_until="domcontentloaded", timeout=15000)
        _rand(page, 1000, 1800)
        _log(on_log, "[티스토리로그아웃] 완료")
        return True
    except Exception as exc:
        _log(on_log, f"[티스토리로그아웃] 실패: {exc}")
        return False
