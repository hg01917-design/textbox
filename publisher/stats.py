"""블로그 조회수 조회.

네이버·티스토리는 공식 3rd-party 통계 API가 없어 로그인된 Chrome으로 각
플랫폼의 관리자 통계 페이지를 스크래핑한다. 페이지 구조는 플랫폼이 자주
바꾸므로, 특정 클래스명 대신 화면에 보이는 "오늘"/조회수 관련 텍스트를 폭넓게
찾는 방식으로 만들어졌다 — 그래도 실제 구조가 크게 바뀌면 파싱에 실패할 수
있으니 실패 시 원인을 바로 알 수 있도록 현재 URL/오류를 그대로 로그에 남긴다.

워드프레스는 Jetpack 통계 모듈이 활성화되어 있으면 자기 사이트의 REST API로
(기존 WP_APP_PASSWORD 인증 그대로) 오늘 조회수를 가져온다.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request

from .accounts import ensure_chrome_for, login_account_id, login_hint
from .browser import connect, get_page
from .login import ensure_naver_login, ensure_tistory_login, logout_naver


def _log(on_log, message: str) -> None:
    if on_log:
        on_log(message)


def _looks_like_login_url(url: str) -> bool:
    lowered = url.lower()
    return "nid.naver.com" in lowered or "login" in lowered or "auth.tistory.com" in lowered


def _extract_count_near_keywords(page, keywords: list[str]) -> int | None:
    """페이지 텍스트에서 keywords 중 하나와 가까이 있는 첫 숫자를 찾는다."""
    texts = []
    try:
        texts.append(page.evaluate("() => document.body.innerText || ''"))
    except Exception:
        pass
    for frame in getattr(page, "frames", []):
        try:
            texts.append(frame.evaluate("() => document.body.innerText || ''"))
        except Exception:
            continue
    for text in texts:
        for keyword in keywords:
            # "오늘 방문자수 123" / "조회수\n실시간\n123" 등 다양한 배치 허용
            pattern = re.compile(re.escape(keyword) + r"[^0-9]{0,40}?([0-9][0-9,]*)")
            m = pattern.search(text)
            if m:
                try:
                    return int(m.group(1).replace(",", ""))
                except ValueError:
                    continue
    return None


def naver_today_views(blog_id: str, on_log=None) -> dict:
    """네이버 블로그 관리자 통계에서 오늘 조회수를 가져온다.

    Returns: {"ok": bool, "views": int, "error": str}
    """
    try:
        port = ensure_chrome_for("naver", blog_id, on_log=on_log)
    except Exception as exc:
        return {"ok": False, "error": f"Chrome 실행 실패: {exc}"}

    pw = browser = None
    try:
        pw, browser = connect(port=port)
        page = get_page(browser)
        account_id = login_account_id("naver", blog_id)
        _log(on_log, f"[홈통계] 네이버 통계 계정 전환 — 저장 계정 '{account_id}' 선택")
        logout_naver(page, on_log=on_log)
        if not ensure_naver_login(page, blog_id, account_id=account_id, on_log=on_log):
            hint = login_hint("naver", blog_id)
            hint_text = f" — 저장된 계정 중 '{hint}'로 로그인하세요." if hint else ""
            return {
                "ok": False,
                "error": f"자동 로그인 실패 — 포트 {port} Chrome 창에서 직접 확인해주세요.{hint_text}",
            }
        page.goto(f"https://admin.blog.naver.com/{blog_id}/stat/today", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        views = _extract_count_near_keywords(page, ["오늘 조회수", "오늘조회수", "조회수", "오늘 방문자수", "오늘방문자수"])
        if views is None:
            return {
                "ok": False,
                "error": f"조회수 파싱 실패 — 통계 페이지 구조를 확인해야 합니다 (현재 URL: {page.url}).",
            }
        return {"ok": True, "views": views}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            if pw:
                pw.stop()
        except Exception:
            pass


def tistory_today_views(blog_id: str, on_log=None) -> dict:
    """티스토리 관리자 통계에서 오늘 조회수를 가져온다.

    Returns: {"ok": bool, "views": int, "error": str}
    """
    try:
        port = ensure_chrome_for("tistory", blog_id, on_log=on_log)
    except Exception as exc:
        return {"ok": False, "error": f"Chrome 실행 실패: {exc}"}

    pw = browser = None
    try:
        pw, browser = connect(port=port)
        page = get_page(browser, navigate_to=f"https://{blog_id}.tistory.com/manage/statistics/blog")
        time.sleep(2)

        if _looks_like_login_url(page.url):
            kakao_id = login_account_id("tistory", blog_id)
            _log(on_log, f"[홈통계] 티스토리 로그인 필요 — 저장 계정 '{kakao_id}' 자동 선택 시도")
            if not ensure_tistory_login(page, blog_id, kakao_id=kakao_id, on_log=on_log):
                hint = login_hint("tistory", blog_id)
                hint_text = f" — 저장된 {hint}으로 로그인하세요." if hint else ""
                return {
                    "ok": False,
                    "error": f"자동 로그인 실패 — 포트 {port} Chrome 창에서 직접 확인해주세요.{hint_text}",
                }
            page.goto(f"https://{blog_id}.tistory.com/manage/statistics/blog", wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

        views = _extract_count_near_keywords(page, ["오늘", "today"])
        if views is None:
            return {
                "ok": False,
                "error": f"조회수 파싱 실패 — 통계 페이지 구조를 확인해야 합니다 (현재 URL: {page.url}).",
            }
        return {"ok": True, "views": views}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            if pw:
                pw.stop()
        except Exception:
            pass


def wp_today_views(site_url: str, user: str, app_password: str) -> dict:
    """Jetpack 통계 모듈(REST API)에서 오늘 조회수를 가져온다.

    사이트에 Jetpack이 설치되고 통계 모듈이 활성화되어 있어야 한다. 기존
    WordPress REST API 인증(Application Password)을 그대로 사용한다.

    Returns: {"ok": bool, "views": int, "error": str}
    """
    site_url = site_url.strip().rstrip("/")
    auth = base64.b64encode(f"{user}:{app_password.replace(' ', '')}".encode("utf-8")).decode("ascii")
    url = f"{site_url}/wp-json/jetpack/v4/module/stats/data?range=day"
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:200]
        if exc.code == 404:
            return {
                "ok": False,
                "error": "Jetpack 통계 API 없음(404) — 워드프레스 Jetpack 통계 모듈이 꺼져 있거나 해당 사이트에서 지원하지 않습니다.",
            }
        return {"ok": False, "error": f"Jetpack 통계 API 오류 ({exc.code}): {detail}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    try:
        days = payload.get("general", {}).get("data", {}).get("days", {})
        if not days:
            return {"ok": False, "error": "Jetpack 통계 응답에 데이터가 없습니다 — 통계 모듈이 켜져 있는지 확인하세요."}
        latest_day = sorted(days.keys())[-1]
        views = int(days[latest_day].get("views", 0))
        return {"ok": True, "views": views}
    except Exception as exc:
        return {"ok": False, "error": f"Jetpack 응답 파싱 실패: {exc}"}
