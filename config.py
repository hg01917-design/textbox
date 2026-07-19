from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DRAFTS_DIR = BASE_DIR / "drafts"
DATA_DIR = BASE_DIR / "data"
PROMPTS_DIR = BASE_DIR / "prompts"
IMAGES_DIR = BASE_DIR / "images"


def load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def read_env_values() -> dict[str, str]:
    values = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def save_env_values(updates: dict[str, str]) -> None:
    existing_lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    updated_keys = set()
    new_lines = []

    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    if new_lines and new_lines[-1].strip():
        new_lines.append("")
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    for key, value in updates.items():
        os.environ[key] = value


# WP_BLOG_IDS의 각 항목은 "triplog"처럼 실제로는 다른 계정과 같은 워드프레스
# 사이트를 가리키는 표시 이름일 수 있다 — 그런 항목은 자체 자격증명이 없으면
# 아래 별칭을 통해 원본 계정의 자격증명을 그대로 사용한다.
_LEGACY_WP_CREDENTIAL_ALIASES = {"triplog": "baremi542"}


def _env_prefix(blog_id: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in blog_id.upper())


def wp_blog_ids() -> list[str]:
    raw = read_env_values().get("WP_BLOG_IDS", "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def wp_credentials(blog_id: str) -> dict:
    values = read_env_values()
    prefix = _env_prefix(blog_id)
    site_url = values.get(f"{prefix}_WP_URL", "")
    user = values.get(f"{prefix}_WP_USER", "")
    app_password = values.get(f"{prefix}_WP_APP_PASSWORD", "")
    if not (site_url and user and app_password):
        alias = _LEGACY_WP_CREDENTIAL_ALIASES.get(blog_id)
        if alias:
            alias_prefix = _env_prefix(alias)
            site_url = site_url or values.get(f"{alias_prefix}_WP_URL", "")
            user = user or values.get(f"{alias_prefix}_WP_USER", "")
            app_password = app_password or values.get(f"{alias_prefix}_WP_APP_PASSWORD", "")
    return {"site_url": site_url, "user": user, "app_password": app_password}


def add_wp_account(blog_id: str, site_url: str, user: str, app_password: str) -> None:
    ids = wp_blog_ids()
    updates = {}
    if blog_id not in ids:
        ids.append(blog_id)
        updates["WP_BLOG_IDS"] = ",".join(ids)
    prefix = _env_prefix(blog_id)
    updates[f"{prefix}_WP_URL"] = site_url
    updates[f"{prefix}_WP_USER"] = user
    updates[f"{prefix}_WP_APP_PASSWORD"] = app_password
    save_env_values(updates)


def remove_wp_account(blog_id: str) -> None:
    ids = [x for x in wp_blog_ids() if x != blog_id]
    save_env_values({"WP_BLOG_IDS": ",".join(ids)})


BLOG_PROFILES = {
    "정부지원": {
        "theme": "정부지원금, 복지, 신청 조건, 지원 대상",
        "max_competition": 50_000,
        "tone": "정확하고 실용적인 안내문",
    },
    "여행": {
        "theme": "여행지, 코스, 숙소, 교통, 예약 팁",
        "max_competition": 30_000,
        "tone": "경험 기반의 친절한 여행 가이드",
    },
    "IT": {
        "theme": "IT, 앱, 기기, 서비스 비교, 사용법",
        "max_competition": 20_000,
        "tone": "명확하고 비교 중심의 설명문",
    },
    "생활정보": {
        "theme": "살림, 절약, 생활 팁, 가정 관리",
        "max_competition": 50_000,
        "tone": "쉽게 따라 할 수 있는 생활 정보 글",
    },
    "일반": {
        "theme": "범용 블로그 정보 글",
        "max_competition": 50_000,
        "tone": "읽기 쉬운 정보성 글",
    },
    "리뷰": {
        "theme": "생활용품, 가전, 잡화 등 실제 사용 후기·비교",
        "max_competition": 50_000,
        "tone": "실제 써본 사람이 전하는 솔직한 후기",
    },
}


def get_blog_profile(blog_type: str) -> dict:
    return BLOG_PROFILES.get(blog_type, BLOG_PROFILES["일반"])
