"""
공공 API 전체 데이터 동기화 스크립트.

사용법:
    python3 sync.py              # 정부24 + 복지로 전체
    python3 sync.py --gov24      # 정부24만
    python3 sync.py --bokjiro    # 복지로만
    python3 sync.py --force      # 캐시 무시하고 재다운로드

저장 경로:
    data/gov24_all.json
    data/bokjiro_all.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from config import load_env

DATA_DIR = Path(__file__).parent / "data"
GOV24_CACHE    = DATA_DIR / "gov24_all.json"
BOKJIRO_CACHE  = DATA_DIR / "bokjiro_all.json"

GOV24_LIST_URL   = "https://api.odcloud.kr/api/gov24/v3/serviceList"
BOKJIRO_LIST_URL = "http://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001"

PER_PAGE = 100


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--gov24",   action="store_true")
    parser.add_argument("--bokjiro", action="store_true")
    parser.add_argument("--force",   action="store_true")
    args = parser.parse_args()

    do_all = not args.gov24 and not args.bokjiro

    if do_all or args.gov24:
        sync_gov24(force=args.force)
    if do_all or args.bokjiro:
        sync_bokjiro(force=args.force)
    return 0


# ─── 정부24 ──────────────────────────────────────────────────────

def sync_gov24(force: bool = False) -> None:
    if not force and GOV24_CACHE.exists():
        data = json.loads(GOV24_CACHE.read_text(encoding="utf-8"))
        print(f"[정부24] 캐시 사용 ({len(data)}건) → {GOV24_CACHE}")
        return

    key = _public_data_key()
    if not key:
        print("[정부24] PUBLIC_DATA_API_KEY 없음 — 건너뜀")
        return

    print("[정부24] 전체 데이터 다운로드 시작...")
    all_items: list[dict] = []
    page = 1
    total = None

    while True:
        params = {
            "serviceKey": key,
            "page": str(page),
            "perPage": str(PER_PAGE),
            "_type": "json",
        }
        data = _get_json(GOV24_LIST_URL, params)
        items = data.get("data", []) if isinstance(data, dict) else []

        if total is None:
            total = int(data.get("totalCount", 0))
            print(f"[정부24] 총 {total}건, {_pages(total, PER_PAGE)}페이지")

        if not items:
            break

        all_items.extend(items)
        pct = len(all_items) / total * 100 if total else 0
        print(f"\r[정부24] {len(all_items)}/{total} ({pct:.0f}%)", end="", flush=True)

        if len(all_items) >= total or len(items) < PER_PAGE:
            break
        page += 1
        time.sleep(0.15)

    print()
    DATA_DIR.mkdir(exist_ok=True)
    GOV24_CACHE.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[정부24] 저장 완료: {len(all_items)}건 → {GOV24_CACHE}")


# ─── 복지로 ──────────────────────────────────────────────────────

def sync_bokjiro(force: bool = False) -> None:
    if not force and BOKJIRO_CACHE.exists():
        data = json.loads(BOKJIRO_CACHE.read_text(encoding="utf-8"))
        print(f"[복지로] 캐시 사용 ({len(data)}건) → {BOKJIRO_CACHE}")
        return

    key = _bokjiro_key() or _public_data_key()
    if not key:
        print("[복지로] API 키 없음 — 건너뜀")
        return

    print("[복지로] 전체 데이터 다운로드 시작...")
    all_items: list[dict] = []
    page = 1
    total = None

    while True:
        params = {
            "serviceKey": key,
            "callTp": "L",
            "pageNo": str(page),
            "numOfRows": str(PER_PAGE),
            "srchKeyCode": "001",
            "searchWrd": "",
        }
        raw = _get_bytes(BOKJIRO_LIST_URL, params)
        if not raw:
            break

        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            break

        if total is None:
            total_text = root.findtext("totalCount") or "0"
            total = int(total_text) if total_text.isdigit() else 0
            print(f"[복지로] 총 {total}건, {_pages(total, PER_PAGE)}페이지")

        items = root.findall(".//servList")
        if not items:
            break

        for item in items:
            record: dict = {}
            for child in item:
                record[child.tag] = (child.text or "").strip()
            all_items.append(record)

        pct = len(all_items) / total * 100 if total else 0
        print(f"\r[복지로] {len(all_items)}/{total} ({pct:.0f}%)", end="", flush=True)

        if total and len(all_items) >= total or len(items) < PER_PAGE:
            break
        page += 1
        time.sleep(0.15)

    print()
    DATA_DIR.mkdir(exist_ok=True)
    BOKJIRO_CACHE.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[복지로] 저장 완료: {len(all_items)}건 → {BOKJIRO_CACHE}")


# ─── 유틸 ────────────────────────────────────────────────────────

def _pages(total: int, per: int) -> int:
    return (total + per - 1) // per


def _get_json(url: str, params: dict) -> dict:
    raw = _get_bytes(url, params)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _get_bytes(url: str, params: dict) -> bytes:
    full_url = url + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(
            full_url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json, application/xml"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read(5_000_000)
    except Exception as e:
        print(f"\n[오류] {e}")
        return b""


def _public_data_key() -> str:
    raw = os.environ.get("PUBLIC_DATA_API_KEY", "").strip()
    return urllib.parse.unquote(raw) if raw else ""


def _bokjiro_key() -> str:
    raw = os.environ.get("BOKJIRO_API_KEY", "").strip()
    return urllib.parse.unquote(raw) if raw else ""


if __name__ == "__main__":
    raise SystemExit(main())
