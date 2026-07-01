from __future__ import annotations

import base64
import json
import os
import re
import ssl
import urllib.parse
import urllib.request

from content.final_check import final_check
from content.html_renderer import render_html


def configured() -> tuple[bool, list[str]]:
    required = ["WP_SITE_URL", "WP_USER", "WP_APP_PASSWORD"]
    missing = [key for key in required if not os.environ.get(key, "").strip()]
    return not missing, missing


def markdown_to_html(markdown: str) -> str:
    return render_html(markdown)


def create_post(title: str, content_markdown: str, tags: list[str] | None = None,
                status: str | None = None, category: str = "", image_paths: list[str] | None = None,
                site_url: str = "", user: str = "", app_password: str = "") -> dict:
    if not site_url:
        ok, missing = configured()
        if not ok:
            return {"ok": False, "error": f"Missing WordPress settings: {', '.join(missing)}"}
        site_url = os.environ["WP_SITE_URL"].strip().rstrip("/")
        user = os.environ["WP_USER"].strip()
        app_password = os.environ["WP_APP_PASSWORD"].replace(" ", "").strip()
    else:
        site_url = site_url.strip().rstrip("/")
        user = user.strip()
        app_password = app_password.replace(" ", "").strip()
    post_status = (status or os.environ.get("WP_DEFAULT_STATUS", "draft") or "draft").strip()
    if post_status not in {"draft", "publish"}:
        post_status = "draft"

    auth = base64.b64encode(f"{user}:{app_password}".encode("utf-8")).decode("ascii")
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    media = []
    for image_path in image_paths or []:
        uploaded = upload_media(site_url, auth, image_path)
        if uploaded.get("ok"):
            media.append(uploaded)
        else:
            return {"ok": False, "error": f"Image upload failed: {uploaded.get('error')}"}
    image_urls = [item["url"] for item in media if item.get("url")]
    content_html = render_html(content_markdown, image_urls=image_urls)
    checked = final_check(title, content_markdown, content_html, image_urls=image_urls)
    if not checked["passed"]:
        return {"ok": False, "error": f"Final check failed: {', '.join(checked['warnings'])}"}
    tag_ids = _resolve_tags(site_url, headers, tags or [])
    category_ids = _resolve_categories(site_url, headers, [category] if category else [])
    body = {
        "title": title,
        "content": content_html,
        "status": post_status,
        "tags": tag_ids,
        "categories": category_ids,
    }
    if media:
        body["featured_media"] = media[0]["id"]
    try:
        req = urllib.request.Request(
            f"{site_url}/wp-json/wp/v2/posts",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        data = json.loads(_urlopen(req, timeout=30).read())
        return {
            "ok": True,
            "id": data.get("id"),
            "link": data.get("link", ""),
            "status": data.get("status", post_status),
            "media_count": len(media),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _inline(text: str) -> str:
    text = _escape_html(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    return text


def upload_media(site_url: str, auth: str, image_path: str) -> dict:
    filename = os.path.basename(image_path)
    try:
        data = open(image_path, "rb").read()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    ext = filename.rsplit(".", 1)[-1].lower()
    mime = "image/png" if ext == "png" else "image/jpeg"
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": mime,
    }
    try:
        req = urllib.request.Request(
            f"{site_url}/wp-json/wp/v2/media",
            data=data,
            headers=headers,
            method="POST",
        )
        uploaded = json.loads(_urlopen(req, timeout=45).read())
        return {"ok": True, "id": uploaded.get("id"), "url": uploaded.get("source_url", "")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _urlopen(req, timeout=15):
    ctx = ssl.create_default_context()
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _resolve_tags(site_url: str, headers: dict, tags: list[str]) -> list[int]:
    ids = []
    for tag in tags[:10]:
        tag = tag.strip()
        if not tag:
            continue
        tag_id = _find_or_create_taxonomy(site_url, headers, "tags", tag)
        if tag_id:
            ids.append(tag_id)
    return ids


def _resolve_categories(site_url: str, headers: dict, categories: list[str]) -> list[int]:
    ids = []
    for category in categories[:3]:
        category = category.strip()
        if not category:
            continue
        cat_id = _find_or_create_taxonomy(site_url, headers, "categories", category)
        if cat_id:
            ids.append(cat_id)
    return ids


def _find_or_create_taxonomy(site_url: str, headers: dict, taxonomy: str, name: str) -> int | None:
    try:
        search_url = f"{site_url}/wp-json/wp/v2/{taxonomy}?search={urllib.parse.quote(name)}"
        req = urllib.request.Request(search_url, headers=headers)
        result = json.loads(_urlopen(req, timeout=10).read())
        if result:
            return int(result[0]["id"])
        create_req = urllib.request.Request(
            f"{site_url}/wp-json/wp/v2/{taxonomy}",
            data=json.dumps({"name": name}, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        created = json.loads(_urlopen(create_req, timeout=10).read())
        return int(created["id"])
    except Exception:
        return None
