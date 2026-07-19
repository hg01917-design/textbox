"""100만원 챌린지 진행 상황 트래킹 — 판매 보고, 콘텐츠 사이클 시각, 대기 중인 승인 요청을 기록한다."""
from __future__ import annotations

import json
import os
from datetime import datetime

from config import DATA_DIR

STATE_PATH = DATA_DIR / "challenge_state.json"
LOCK_PATH = DATA_DIR / "challenge_lock.json"
LOCK_STALE_MINUTES = 20
GOAL_KRW = 1_000_000

_DEFAULT_STATE = {
    "goal_krw": GOAL_KRW,
    "revenue_krw": 0,
    "sales": [],
    "last_cycle_at": "",
    "pending_action": None,
}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return dict(_DEFAULT_STATE)
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        merged = dict(_DEFAULT_STATE)
        merged.update(data)
        return merged
    except Exception:
        return dict(_DEFAULT_STATE)


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def record_sale(amount_krw: int, note: str = "") -> dict:
    state = load_state()
    state["revenue_krw"] = state.get("revenue_krw", 0) + amount_krw
    state.setdefault("sales", []).append({
        "at": datetime.now().isoformat(timespec="seconds"),
        "amount_krw": amount_krw,
        "note": note,
    })
    save_state(state)
    return state


def set_pending_action(description: str, action_type: str) -> None:
    state = load_state()
    state["pending_action"] = {
        "description": description,
        "action_type": action_type,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_state(state)


def clear_pending_action() -> None:
    state = load_state()
    state["pending_action"] = None
    save_state(state)


def mark_cycle_ran() -> None:
    state = load_state()
    state["last_cycle_at"] = datetime.now().isoformat(timespec="seconds")
    save_state(state)


def hours_since_last_cycle() -> float:
    state = load_state()
    last = state.get("last_cycle_at", "")
    if not last:
        return 999.0
    try:
        delta = datetime.now() - datetime.fromisoformat(last)
        return delta.total_seconds() / 3600
    except Exception:
        return 999.0


def acquire_lock() -> bool:
    """실행 중복 방지 락. 다른 실행이 이미 진행 중이면 False를 반환한다.

    5분마다 도는 스케줄 작업이 한 사이클 처리 시간(콘텐츠 생성 등)보다 짧은
    간격으로 겹쳐 돌면(예: 앱이 오래 꺼져있다 켜지며 밀린 실행이 한번에 몰릴 때)
    중복 초안 생성/중복 텔레그램 발송이 발생한다. 락 파일을 원자적으로 생성해
    막는다. 이전 실행이 비정상 종료해 락을 못 지운 경우를 대비해
    LOCK_STALE_MINUTES가 지나면 오래된 락으로 보고 새로 점유한다.
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    payload = json.dumps({"started_at": now.isoformat(timespec="seconds"), "pid": os.getpid()}).encode("utf-8")
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        return True
    except FileExistsError:
        stale = True
        try:
            data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            started = datetime.fromisoformat(data["started_at"])
            stale = (now - started).total_seconds() / 60 >= LOCK_STALE_MINUTES
        except Exception:
            stale = True
        if not stale:
            return False
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass
        return acquire_lock()


def release_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass
