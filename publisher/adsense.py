"""Google AdSense Management API 연동 — OAuth2 인증 + 오늘 추정 수익 조회.

최초 1회, 사용자가 직접 브라우저에서 구글 계정으로 로그인하고 동의해야 한다
(start_oauth_flow가 기본 브라우저를 열어준다 — 로그인/동의는 항상 사용자가
직접 함). 이후에는 발급받은 refresh token을 .env에 저장해두고 재사용한다.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from config import read_env_values, save_env_values

SCOPE = "https://www.googleapis.com/auth/adsense.readonly"
REDIRECT_PORT = 8918
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/oauth2callback"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REFRESH_TOKEN_KEY = "GOOGLE_ADSENSE_REFRESH_TOKEN"


def _client_id_secret() -> tuple[str, str]:
    values = read_env_values()
    return values.get("GOOGLE_CLIENT_ID", "").strip(), values.get("GOOGLE_CLIENT_SECRET", "").strip()


def is_connected() -> bool:
    return bool(read_env_values().get(REFRESH_TOKEN_KEY, "").strip())


class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        _CallbackHandler.result["code"] = params.get("code", [""])[0]
        _CallbackHandler.result["error"] = params.get("error", [""])[0]
        ok = bool(_CallbackHandler.result["code"])
        message = "애드센스 연동 완료 — 이 창은 닫으셔도 됩니다." if ok else "연동 실패 — Claude로 돌아가 오류를 확인하세요."
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<html><body><h3>{message}</h3></body></html>".encode("utf-8"))

    def log_message(self, *args):
        pass


def start_oauth_flow(on_done=None) -> None:
    """기본 브라우저로 구글 동의 화면을 열고, 백그라운드에서 콜백을 기다려 토큰을 저장한다.

    on_done(ok: bool, message: str)가 완료(성공/실패) 시 별도 스레드에서 호출된다.
    """
    import webbrowser

    client_id, client_secret = _client_id_secret()
    if not client_id or not client_secret:
        if on_done:
            on_done(False, "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET이 .env에 없습니다.")
        return

    _CallbackHandler.result = {}
    server = HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    webbrowser.open(f"{AUTH_URL}?{urllib.parse.urlencode(params)}")

    def _wait_and_exchange():
        server.timeout = 300
        server.handle_request()
        code = _CallbackHandler.result.get("code")
        if not code:
            error = _CallbackHandler.result.get("error") or "타임아웃 또는 사용자가 취소했습니다."
            if on_done:
                on_done(False, error)
            return
        try:
            _exchange_code(code, client_id, client_secret)
            if on_done:
                on_done(True, "")
        except Exception as exc:
            if on_done:
                on_done(False, str(exc))

    threading.Thread(target=_wait_and_exchange, daemon=True).start()


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Google OAuth 오류 ({exc.code}): {detail[:300]}") from exc


def _exchange_code(code: str, client_id: str, client_secret: str) -> None:
    payload = _post_form(TOKEN_URL, {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    })
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(
            "refresh_token을 받지 못했습니다. 이전에 이미 연동한 적이 있다면 "
            "Google 계정 > 보안 > 타사 앱 액세스에서 기존 연동을 해제하고 다시 시도하세요."
        )
    save_env_values({REFRESH_TOKEN_KEY: refresh_token})


def _access_token() -> str:
    client_id, client_secret = _client_id_secret()
    refresh_token = read_env_values().get(REFRESH_TOKEN_KEY, "").strip()
    if not refresh_token:
        raise RuntimeError("애드센스 연동이 되어 있지 않습니다. 설정에서 먼저 연동하세요.")
    payload = _post_form(TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"액세스 토큰 발급 실패: {payload}")
    return token


def _get_json(url: str, access_token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"AdSense API 오류 ({exc.code}): {detail[:300]}") from exc


def _first_account_name(access_token: str) -> str:
    payload = _get_json("https://adsense.googleapis.com/v2/accounts", access_token)
    accounts = payload.get("accounts", [])
    if not accounts:
        raise RuntimeError("연결된 애드센스 계정을 찾을 수 없습니다.")
    return accounts[0]["name"]  # 예: "accounts/pub-1234567890"


def today_earnings() -> dict:
    """오늘 추정 수익. Returns: {"ok": bool, "amount": str, "currency": str, "error": str}"""
    try:
        token = _access_token()
        account = _first_account_name(token)
        params = urllib.parse.urlencode({
            "dateRange": "TODAY",
            "metrics": "ESTIMATED_EARNINGS",
        })
        payload = _get_json(
            f"https://adsense.googleapis.com/v2/{account}/reports:generate?{params}", token
        )
        rows = payload.get("rows", [])
        if not rows:
            return {"ok": True, "amount": "0", "currency": ""}
        cell = rows[0]["cells"][0]
        return {"ok": True, "amount": cell.get("value", "0"), "currency": ""}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
