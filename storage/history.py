from __future__ import annotations

import json
from datetime import datetime

from config import DATA_DIR

HISTORY_PATH = DATA_DIR / "publish_log.jsonl"


def record_publish(platform: str, blog_id: str, title: str) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "platform": platform,
        "blog_id": blog_id,
        "title": title,
    }
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def count_today_publishes(platform: str | None = None, blog_id: str | None = None) -> int:
    if not HISTORY_PATH.exists():
        return 0
    today = datetime.now().date().isoformat()
    count = 0
    for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if not entry.get("at", "").startswith(today):
            continue
        if platform and entry.get("platform") != platform:
            continue
        if blog_id and entry.get("blog_id") != blog_id:
            continue
        count += 1
    return count
