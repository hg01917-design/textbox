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
                site_url: str = "", user: str = "", app_password: str = "", post_id: int = 0) -> dict:
    """post_id를 지정하면 새 글을 만드는 대신 해당 글을 수정한다 (WordPress REST API는 기존 글 수정을 지원)."""
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
    # media[0]은 featured_media(제목 위 대표 이미지)로 쓰이므로, 본문 안에 또
    # 넣으면 같은 이미지가 두 번 보인다 — 본문용 목록에서는 제외한다.
    inline_media = media[1:] if media else media
    image_urls = [item["url"] for item in inline_media if item.get("url")]
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
        endpoint = f"{site_url}/wp-json/wp/v2/posts/{post_id}" if post_id else f"{site_url}/wp-json/wp/v2/posts"
        req = urllib.request.Request(
            endpoint,
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


def _ascii_filename(filename: str) -> str:
    stem, _, ext = filename.rpartition(".")
    ascii_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")
    ascii_stem = re.sub(r"-{2,}", "-", ascii_stem)
    if not ascii_stem:
        ascii_stem = "image"
    return f"{ascii_stem}.{ext}" if ext else ascii_stem


def upload_media(site_url: str, auth: str, image_path: str) -> dict:
    filename = os.path.basename(image_path)
    try:
        data = open(image_path, "rb").read()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    ext = filename.rsplit(".", 1)[-1].lower()
    mime = "image/png" if ext == "png" else "image/jpeg"
    # 카드 이미지 파일명은 한글을 포함하는데, HTTP 헤더는 latin-1로만 인코딩
    # 가능해 한글 filename을 그대로 넣으면 요청 전송 시 UnicodeEncodeError가 난다.
    # WordPress REST API의 미디어 엔드포인트는 RFC 5987 확장 인코딩
    # (filename*=UTF-8''...)도 받아주지 않고 정확히
    # `filename="..."` 형식만 허용하므로, 헤더에는 ASCII로 치환한
    # 파일명만 넣는다 (실제 업로드되는 파일 내용은 그대로).
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Disposition": f'attachment; filename="{_ascii_filename(filename)}"',
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
