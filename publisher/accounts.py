"""블로그 계정 관리 + 단일 Chrome 9222 프로필 실행.

네이버·카카오(티스토리)는 하나의 브라우저에서 한 계정만 로그인된다. 이 앱은
blog-automation 방식처럼 포트를 여러 개 만들지 않고, CHROME_PORT(기본 9222)
하나에 연결한 뒤 저장된 계정 목록에서 필요한 계정을 선택해 로그인/로그아웃한다.

identity 값은 더 이상 포트 분리에 쓰지 않고, 로그인 화면에서 어떤 저장 계정을
선택해야 하는지 알려주는 값으로만 사용한다.
"""
from __future__ import annotations

import subprocess
import time
import os
from pathlib import Path

from config import read_env_values, save_env_values

# blog_id → 실제 로그인 계정(identity). 실측(blog-automation-v2 config.py)으로 확인.
# 네이버는 계정 자체, 티스토리는 카카오 로그인 기준 — nolja100·phn0502는 같은
# 카카오 계정(baremi542)이라 포트를 공유해도 된다.
_LEGACY_IDENTITY = {
    ("naver", "salim1su"): "daonna525",
    ("naver", "daonna525"): "daonna525",
    ("naver", "me1091"): "me1091_naver",
    ("tistory", "goodisak"): "isag27511_kakao",
    ("tistory", "nolja100"): "baremi542_kakao",
    ("tistory", "phn0502"): "baremi542_kakao",
    ("tistory", "woll100"): "wolbaeg100_kakao",
}

# identity → 사람이 보는 안내 문구 (로그인 시 어떤 저장된 계정을 눌러야 하는지).
_IDENTITY_LABELS = {
    "daonna525": "daonna525",
    "me1091_naver": "me1091",
    "isag27511_kakao": "카카오 계정 isag27511",
    "baremi542_kakao": "카카오 계정 baremi542",
    "wolbaeg100_kakao": "카카오 계정 wolbaeg100",
}

_SHARED_PROFILE_DIR = Path.home() / "Library" / "Application Support" / "Google" / "ChromeDebug"
_CHROME_BIN_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
]


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name.upper())


def _blog_ids(env_key: str) -> list[str]:
    raw = read_env_values().get(env_key, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def naver_blog_ids() -> list[str]:
    return _blog_ids("NAVER_BLOG_IDS")


def tistory_blog_ids() -> list[str]:
    return _blog_ids("TISTORY_BLOG_IDS")


def _identity_key(platform: str, blog_id: str) -> str:
    return f"{platform.upper()}_{_safe(blog_id)}_IDENTITY"


def identity_for(platform: str, blog_id: str) -> str:
    """이 블로그가 실제로 어느 로그인 계정에 묶여 있는지 식별자를 반환.

    저장된 값이 없으면 blog_id 자신을 식별자로 쓴다(= 이 블로그 전용 프로필).
    """
    stored = read_env_values().get(_identity_key(platform, blog_id), "").strip()
    if stored:
        return stored
    legacy = _LEGACY_IDENTITY.get((platform, blog_id))
    if legacy:
        return legacy
    return f"{platform}:{blog_id}"


def set_identity(platform: str, blog_id: str, identity: str) -> None:
    if identity.strip():
        save_env_values({_identity_key(platform, blog_id): identity.strip()})


def login_hint(platform: str, blog_id: str) -> str:
    """"로그인 필요" 상태일 때 어떤 저장된 계정을 눌러야 하는지 안내 문구(참고용).

    자동 로그인에 쓰이지 않는다 — 사용자가 크롬에서 직접 고를 때 헷갈리지
    않도록 알려주는 용도일 뿐이다.
    """
    identity = identity_for(platform, blog_id)
    return _IDENTITY_LABELS.get(identity, identity if ":" not in identity else "")


def login_account_id(platform: str, blog_id: str) -> str:
    """저장 계정 목록에서 실제로 찾을 로그인 ID 문자열."""
    identity = identity_for(platform, blog_id)
    label = _IDENTITY_LABELS.get(identity, identity)
    label = label.replace("카카오 계정", "").strip()
    if ":" in label:
        label = label.split(":", 1)[1].strip()
    if label.endswith("_kakao"):
        label = label[:-6]
    if label.endswith("_naver"):
        label = label[:-6]
    return label


def _identity_port_key(identity: str) -> str:
    return f"LOGIN_{_safe(identity)}_PORT"


def _next_free_port() -> int:
    return _default_port()


def _port_for_identity(identity: str) -> int:
    return _default_port()


def _default_port() -> int:
    raw = read_env_values().get("CHROME_PORT") or os.environ.get("CHROME_PORT") or "9222"
    try:
        return int(raw)
    except ValueError:
        return 9222


def naver_port(blog_id: str) -> int:
    return _port_for_identity(identity_for("naver", blog_id))


def tistory_port(blog_id: str) -> int:
    return _port_for_identity(identity_for("tistory", blog_id))


def add_naver_account(blog_id: str, isolated: bool = False, login_hint_text: str = "") -> int:
    """네이버 계정을 NAVER_BLOG_IDS에 추가한다.

    login_hint_text: 이 블로그가 실제로 어떤 로그인 계정에 묶여있는지(예:
    "daonna525"). 이미 등록된 다른 블로그와 같은 값을 쓰면 같은 프로필/포트를
    공유한다(둘 다 같은 계정 소유일 때만!). 비워두면 이 블로그 전용 새 프로필이
    배정된다 — isolated 인자는 하위호환용으로 남겨두되 실질적으로 항상 "이
    blog_id 자신을 identity로 쓴다(=격리)"와 같다.
    """
    ids = naver_blog_ids()
    if blog_id not in ids:
        ids.append(blog_id)
        save_env_values({"NAVER_BLOG_IDS": ",".join(ids)})
    if login_hint_text:
        set_identity("naver", blog_id, login_hint_text)
    return naver_port(blog_id)


def add_tistory_account(blog_id: str, isolated: bool = False, login_hint_text: str = "") -> int:
    ids = tistory_blog_ids()
    if blog_id not in ids:
        ids.append(blog_id)
        save_env_values({"TISTORY_BLOG_IDS": ",".join(ids)})
    if login_hint_text:
        set_identity("tistory", blog_id, login_hint_text)
    return tistory_port(blog_id)


def remove_naver_account(blog_id: str) -> None:
    ids = [x for x in naver_blog_ids() if x != blog_id]
    save_env_values({"NAVER_BLOG_IDS": ",".join(ids)})


def remove_tistory_account(blog_id: str) -> None:
    ids = [x for x in tistory_blog_ids() if x != blog_id]
    save_env_values({"TISTORY_BLOG_IDS": ",".join(ids)})


def _chrome_binary() -> str:
    for path in _CHROME_BIN_CANDIDATES:
        if Path(path).exists():
            return path
    raise RuntimeError("Google Chrome을 찾을 수 없습니다 (/Applications/Google Chrome.app 확인 필요).")


def _port_open(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def ensure_chrome(port: int, user_data_dir: str = "", profile_key: str = "", on_log=None, timeout: int = 20) -> bool:
    """단일 공유 Chrome CDP 포트를 보장한다."""
    if _port_open(port):
        return True

    if not user_data_dir:
        user_data_dir = read_env_values().get("CHROME_USER_DATA_DIR", "").strip()
    if not user_data_dir:
        user_data_dir = str(_SHARED_PROFILE_DIR)
    if user_data_dir:
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

    if on_log:
        on_log(f"[Chrome] 공유 Chrome 포트 {port} 실행 중 ({user_data_dir})...")

    args = [
        _chrome_binary(),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open(port):
            time.sleep(1)
            return True
        time.sleep(0.5)
    return False


def ensure_chrome_for(platform: str, blog_id: str, on_log=None) -> int:
    """단일 공유 Chrome 포트를 실행하고 반환한다."""
    port = _default_port()
    ensure_chrome(port, on_log=on_log)
    return port
