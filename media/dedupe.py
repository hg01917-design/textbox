from __future__ import annotations

import hashlib
import json
from pathlib import Path

from config import DATA_DIR


MANIFEST_PATH = DATA_DIR / "image_manifest.json"


def file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"images": []}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"images": []}


def register_images(paths: list[str], keyword: str = "") -> tuple[list[str], list[str]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    known = {item.get("hash") for item in manifest.get("images", [])}
    current_seen = set()
    accepted = []
    duplicates = []
    for path in paths:
        digest = file_hash(path)
        if digest in current_seen:
            duplicates.append(path)
            continue
        current_seen.add(digest)
        accepted.append(path)
        if digest not in known:
            known.add(digest)
            manifest.setdefault("images", []).append({"path": str(path), "hash": digest, "keyword": keyword})
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return accepted, duplicates
