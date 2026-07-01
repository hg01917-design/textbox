from __future__ import annotations

import re
import textwrap
from datetime import datetime
from pathlib import Path

from config import IMAGES_DIR
from content.cards import card_items
from .dedupe import register_images


def generate_card_images(keyword: str, blog_type: str, on_log=None) -> list[str]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        if on_log:
            on_log("[카드] Pillow가 없어 카드 이미지를 건너뜁니다")
        return []

    output_dir = IMAGES_DIR / "cards"
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    stamp = datetime.now().strftime("%H%M%S%f")
    for item in card_items(blog_type, keyword):
        filename = f"{_slug(keyword)}-{item['index']}-{_slug(item['subtitle'])}-{stamp}.jpg"
        path = output_dir / filename
        _draw_card(path, item, blog_type, Image, ImageDraw, ImageFont)
        paths.append(str(path))
    accepted, duplicates = register_images(paths, keyword=keyword)
    if on_log:
        on_log(f"[카드] 생성 {len(paths)}장, 중복 제외 {len(duplicates)}장")
    return accepted


def _draw_card(path: Path, item: dict, blog_type: str, Image, ImageDraw, ImageFont) -> None:
    w, h = 1080, 1080
    palettes = {
        "정부지원": ((225, 245, 250), (70, 120, 160)),
        "여행": ((222, 248, 238), (40, 130, 110)),
        "생활정보": ((255, 235, 239), (190, 80, 105)),
        "IT": ((214, 232, 255), (45, 90, 160)),
    }
    bg, accent = palettes.get(blog_type, ((235, 240, 255), (70, 90, 140)))
    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        blend = y / h
        color = tuple(int(bg[i] * (1 - blend) + min(255, bg[i] + 18) * blend) for i in range(3))
        draw.line([(0, y), (w, y)], fill=color)

    title_size, body_size = _fit_font_sizes(item)
    font_title = _font(ImageFont, title_size)
    font_sub = _font(ImageFont, 38)
    font_body = _font(ImageFont, body_size)
    title_wrap = max(14, int(1180 / title_size))
    body_wrap = max(18, int(1180 / body_size))
    title_lines = _wrap(item["title"], title_wrap)[:3]
    bullet_lines = []
    for bullet in item["bullets"][:4]:
        lines = _wrap(bullet, body_wrap)[:2]
        bullet_lines.append(lines)

    content_height = 64
    content_height += _line_height(title_size) * len(title_lines)
    content_height += 36
    for lines in bullet_lines:
        content_height += _line_height(body_size) * len(lines) + 18
    content_height += 54
    card_h = min(h - 140, max(620, content_height))
    top = (h - card_h) // 2
    bottom = top + card_h
    draw.rounded_rectangle((86, top, w - 86, bottom), radius=36, fill=(255, 255, 255), outline=accent, width=5)
    draw.text((126, top + 42), item["subtitle"], font=font_sub, fill=accent)

    y = top + 118
    for line in title_lines:
        draw.text((126, y), line, font=font_title, fill=(20, 35, 50))
        y += _line_height(title_size)
    y += 28
    dot = max(14, body_size // 2)
    for lines in bullet_lines:
        draw.ellipse((132, y + body_size // 3, 132 + dot, y + body_size // 3 + dot), fill=accent)
        line_y = y
        for line in lines:
            draw.text((166, line_y), line, font=font_body, fill=(35, 45, 55))
            line_y += _line_height(body_size)
        y = line_y + 18
    font_brand = _font(ImageFont, 28)
    draw.text((126, bottom - 56), "성실한하루", font=font_brand, fill=(160, 170, 185))
    img.save(path, "JPEG", quality=92, optimize=True)


def _fit_font_sizes(item: dict) -> tuple[int, int]:
    title_len = len(item.get("title", ""))
    bullet_len = max([len(b) for b in item.get("bullets", [])[:4]] or [0])
    title_size = 66
    if title_len > 28:
        title_size = 60
    if title_len > 42:
        title_size = 54
    body_size = 44
    if bullet_len > 28:
        body_size = 40
    if bullet_len > 42:
        body_size = 36
    return title_size, body_size


def _line_height(size: int) -> int:
    return int(size * 1.24)


def _font(ImageFont, size: int):
    for path in ["/System/Library/Fonts/AppleSDGothicNeo.ttc", "/Library/Fonts/NanumGothicBold.ttf"]:
        try:
            return ImageFont.truetype(path, size, index=6)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width=width) or [text]


def _slug(text: str) -> str:
    text = re.sub(r"[^0-9A-Za-z가-힣\s-]", "", text).strip()
    return re.sub(r"\s+", "-", text)[:50] or "card"
