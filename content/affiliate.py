"""제휴(어필리에이트) 링크 자동 생성 — 쿠팡파트너스 / 마이리얼트립.

키워드로 실제 상품·투어를 검색해 제휴 링크를 만들고, 발행 가능한 마크다운
블록(법적 고지문 포함)으로 반환한다. API 키가 없거나 호출이 실패하면 항상
빈 문자열/빈 리스트를 반환해 초안 생성·발행 자체를 막지 않는다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import os
import urllib.error
import urllib.parse
import urllib.request

COUPANG_DOMAIN = "https://api-gateway.coupang.com"
COUPANG_DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."

MRT_SEARCH_URL = "https://www.myrealtrip.com/offers?q={query}"
MRT_API_BASE = "https://partner-ext-api.myrealtrip.com"
MRT_MYLINK_PATH = "/v1/mylink"
MRT_DISCLOSURE = "이 포스팅은 마이리얼트립 제휴 활동을 통해 일정액의 수수료를 제공받을 수 있습니다."

# blog_type → 네이버 SmartEditor "내 템플릿"에 저장된 템플릿 이름.
# 사용자가 SmartEditor에서 직접 만들어 저장한 템플릿만 매핑한다 — 여기 없는
# 유형은 템플릿을 삽입하지 않는다.
NAVER_TEMPLATE_MAP = {
    "여행": "마이리얼트립",
    "리뷰": "본문제목자리",
}

# blog_type → 고지 배너 소스. 티스토리/워드프레스는 네이버 템플릿이 없으니
# get_disclosure_banner()로 만든 이미지를 직접 image_paths에 끼워 넣는다.
DISCLOSURE_SOURCE_MAP = {
    "여행": "mrt",
    "리뷰": "coupang",
}

BLOG_TYPE_CATEGORY_MAP = {
    "생활정보": "생활용품",
    "IT": "전자기기",
    "여행": "여행용품",
    "정부지원": "생활용품",
    "일반": "생활용품",
    "리뷰": "생활용품",
}

_PROMO_PREFIX_RE = re.compile(
    r"^(최저가\s*보장제|즉시\s*확정|무료\s*취소|프로모션|선착순\s*특가|한정\s*특가"
    r"|\[\s*(최저가\s*보장제|즉시\s*확정|무료\s*취소|프로모션|선착순\s*특가|한정\s*특가)\s*\])\s*"
)
_RATING_RE = re.compile(r"\d\.\d\(\d+\)")
_PRICE_RE = re.compile(r"\d{1,3}(,\d{3})*\s*원")
_PERCENT_RE = re.compile(r"\d+\s*%")
_JUNK_CHARS_RE = re.compile(r"[𐤟★☆✓✔️]")


def clean_product_name(name: str) -> str:
    """쿠팡/MRT 원본 상품명에서 프로모션 태그·평점·가격 등 잡음을 제거한다.

    키워드/제목 생성에 원본 상품명을 그대로 쓰면 "최저가보장제...4.8(200)...15,000원"
    처럼 지저분한 문구가 제목에 그대로 섞여 나올 수 있어, 실제 상품/장소를
    가리키는 핵심 문구만 남긴다.
    """
    text = name
    prev = None
    while prev != text:
        prev = text
        text = _PROMO_PREFIX_RE.sub("", text)
    text = _RATING_RE.sub("", text)
    text = _PRICE_RE.sub("", text)
    text = _PERCENT_RE.sub("", text)
    text = _JUNK_CHARS_RE.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -·|/")
    return text or name


def _sanitize_link_text(text: str) -> str:
    """마크다운 링크 텍스트로 쓸 수 있도록 대괄호를 제거한다.

    상품명에 "[즉시확정]" 같은 대괄호 태그가 섞여 있으면 [text](url) 문법이
    중첩 대괄호 때문에 깨지므로, 링크 텍스트에서는 대괄호를 없앤다.
    """
    return text.replace("[", "").replace("]", "").strip()


_NON_PRODUCT_WORDS = {
    "제거", "방법", "하는법", "팁", "절약", "정리", "청소법", "관리", "관리법",
    "사용법", "하기", "이유", "효과", "좋은", "집에서", "간단히", "쉽게", "빠르게",
    "직접", "알아보기", "알아보자", "총정리", "완벽", "비교", "추천", "후기",
    "리뷰", "정보", "기초", "기본", "쉬운", "초보", "따라하기",
}


# ─── 쿠팡파트너스 ─────────────────────────────────────────────────────────

def _coupang_keys() -> tuple[str, str]:
    return (
        os.environ.get("COUPANG_ACCESS_KEY", "").strip(),
        os.environ.get("COUPANG_SECRET_KEY", "").strip(),
    )


def _coupang_hmac_headers(access_key: str, secret_key: str, method: str, path: str, query: str) -> dict:
    datetime_now = time.strftime("%y%m%dT%H%M%SZ", time.gmtime())
    message = datetime_now + method + path + query
    signature = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), digestmod=hashlib.sha256).hexdigest()
    authorization = (
        f"CEA algorithm=HmacSHA256, access-key={access_key}, "
        f"signed-date={datetime_now}, signature={signature}"
    )
    return {"Authorization": authorization}


def create_coupang_affiliate_link(coupang_url: str) -> str:
    """쿠팡 상품 URL을 파트너스 단축 링크(link.coupang.com)로 변환한다.

    이미 파트너스 링크면 그대로, 키가 없거나 실패하면 원본 URL을 반환한다.
    """
    if "link.coupang.com" in coupang_url:
        return coupang_url

    access_key, secret_key = _coupang_keys()
    if not access_key or not secret_key:
        return coupang_url

    try:
        path = "/v2/providers/affiliate_open_api/apis/openapi/deeplink"
        body = json.dumps({"coupangUrls": [coupang_url]}).encode("utf-8")
        headers = _coupang_hmac_headers(access_key, secret_key, "POST", path, "")
        headers["Content-Type"] = "application/json;charset=UTF-8"
        req = urllib.request.Request(f"{COUPANG_DOMAIN}{path}", data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("data", [{}])[0].get("shortenUrl", "") or coupang_url
    except Exception:
        return coupang_url


def search_coupang_products(keyword: str, category: str = "", limit: int = 3) -> list[dict]:
    """쿠팡파트너스 API로 상품을 검색한다. 실패 시 빈 리스트를 반환한다.

    Returns: [{"name": str, "url": str}, ...]
    """
    access_key, secret_key = _coupang_keys()
    if not access_key or not secret_key:
        return []

    try:
        path = "/v2/providers/affiliate_open_api/apis/openapi/products/search"
        query_params = urllib.parse.urlencode({"keyword": keyword, "limit": limit})
        headers = _coupang_hmac_headers(access_key, secret_key, "GET", path, query_params)
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{COUPANG_DOMAIN}{path}?{query_params}", headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        products = []
        for item in data.get("data", {}).get("productData", [])[:limit]:
            products.append({
                "name": item.get("productName", keyword),
                "url": item.get("productUrl", ""),
            })
        return products
    except Exception:
        return []


def _extract_product_keyword(keyword: str) -> str:
    """블로그 키워드에서 쇼핑 검색에 적합한 핵심 상품어를 추출한다.

    Claude CLI로 우선 시도하고, 실패 시 규칙 기반(마지막 비동작어 단어)으로 대체한다.
    """
    try:
        from .generator import _cli_generate
        prompt = (
            f"블로그 키워드 '{keyword}'에서 쿠팡 쇼핑 검색창에 입력할 핵심 상품명만 추출해줘.\n"
            f"검색창에 실제로 입력할 단어 1~2개만 출력. 설명·번호·따옴표 없이 단어만."
        )
        result, _err = _cli_generate(prompt)
        if result:
            extracted = result.strip().splitlines()[0].strip().strip("'\"")
            if extracted and len(extracted) >= 2:
                return extracted
    except Exception:
        pass

    words = keyword.split()
    if len(words) <= 1:
        return keyword
    product_words = [w for w in words if w not in _NON_PRODUCT_WORDS]
    return (product_words or words)[-1]


def get_coupang_affiliate_block(keyword: str, blog_type: str = "일반") -> str:
    """blog_type에 맞는 카테고리로 상품을 검색해 마크다운 링크 블록을 반환한다.

    키가 없거나 검색 결과가 없으면 빈 문자열을 반환한다 (발행 중단 없음).
    """
    access_key, secret_key = _coupang_keys()
    if not access_key or not secret_key:
        return ""

    try:
        category = BLOG_TYPE_CATEGORY_MAP.get(blog_type, "생활용품")
        search_kw = _extract_product_keyword(keyword)
        products = search_coupang_products(search_kw, category=category, limit=2)
        if not products:
            return ""

        lines = ["\n\n---\n\n### 이 글과 함께 많이 본 상품\n"]
        for p in products:
            link = create_coupang_affiliate_link(p["url"]) if p.get("url") else ""
            if not link:
                continue
            lines.append(f"- [{_sanitize_link_text(p['name'])}]({link})")
        if len(lines) == 1:
            return ""
        lines.append(f"\n*{COUPANG_DISCLOSURE}*\n")
        return "\n".join(lines)
    except Exception:
        return ""


# ─── 마이리얼트립 ─────────────────────────────────────────────────────────

def _mrt_api_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ.get('MRT_API_KEY', '')}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }


def create_mrt_affiliate_link(target_url: str) -> str:
    """MRT 공식 API로 targetUrl을 myrealt.rip 단축 제휴 링크로 변환한다.

    키가 없거나 실패하면 빈 문자열을 반환한다.
    """
    api_key = os.environ.get("MRT_API_KEY", "").strip()
    if not api_key or not target_url or not target_url.startswith("http"):
        return ""

    try:
        body = json.dumps({"targetUrl": target_url}).encode("utf-8")
        req = urllib.request.Request(f"{MRT_API_BASE}{MRT_MYLINK_PATH}", data=body, headers=_mrt_api_headers(), method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("data", {}).get("mylink", "") or ""
    except Exception:
        return ""


def _mrt_search_products(keyword: str, top_n: int = 5) -> list[dict]:
    """Playwright(기존 CDP 연결)로 myrealtrip.com에서 상품을 검색한다.

    Chrome이 CDP 모드로 떠 있지 않으면 빈 리스트를 반환한다.
    """
    from publisher.browser import connect, get_page

    try:
        pw, browser = connect()
    except Exception:
        return []

    try:
        q = urllib.parse.quote(keyword)
        page = get_page(browser, navigate_to=MRT_SEARCH_URL.format(query=q))

        for _ in range(10):
            page.wait_for_timeout(1000)
            count = page.evaluate("""() =>
                [...document.querySelectorAll('a[href]')]
                .filter(el => /(experiences|offers)\\.myrealtrip\\.com\\/products\\/\\d+/.test(el.href) ||
                              /myrealtrip\\.com\\/offers\\/\\d+/.test(el.href))
                .length
            """)
            if count > 0:
                break

        products = page.evaluate("""(topN) => {
            const seen = new Set();
            const items = [];
            const cards = document.querySelectorAll(
                'li[class*="offer"], li[class*="product"], div[class*="offer-card"], div[class*="product-card"], article'
            );
            const extractFromCard = (card) => {
                const link = card.querySelector('a[href]');
                if (!link) return null;
                const href = link.href || '';
                if (!/(experiences|offers|products)/.test(href)) return null;
                if (!/\\/\\d{4,}/.test(href)) return null;
                if (seen.has(href)) return null;
                const titleEl = card.querySelector('[class*="title"], [class*="name"], h2, h3');
                const title = (titleEl?.innerText || link.innerText || '').trim().replace(/\\s+/g, ' ').substring(0, 80);
                if (title.length < 5) return null;
                let price = '';
                const priceEl = card.querySelector('[class*="price"], [class*="amount"]');
                if (priceEl) price = priceEl.innerText.trim().replace(/\\s+/g, ' ').substring(0, 30);
                let image = '';
                const imgEl = card.querySelector('img[src]');
                if (imgEl) image = imgEl.currentSrc || imgEl.src || '';
                return { title, url: href, price, image };
            };
            if (cards.length === 0) {
                for (const el of document.querySelectorAll('a[href]')) {
                    const href = el.href || '';
                    if (!/(experiences|offers|products)/.test(href)) continue;
                    if (!/\\/\\d{4,}/.test(href)) continue;
                    if (seen.has(href)) continue;
                    seen.add(href);
                    const title = el.innerText.trim().replace(/\\s+/g, ' ').substring(0, 80);
                    if (title.length < 5) continue;
                    const imgEl = el.querySelector('img[src]') || el.closest('li,div,article')?.querySelector('img[src]');
                    const image = imgEl ? (imgEl.currentSrc || imgEl.src || '') : '';
                    items.push({ title, url: href, price: '', image });
                    if (items.length >= topN) break;
                }
                return items;
            }
            for (const card of cards) {
                const item = extractFromCard(card);
                if (!item) continue;
                seen.add(item.url);
                items.push(item);
                if (items.length >= topN) break;
            }
            return items;
        }""", top_n)
        return products or []
    except Exception:
        return []
    finally:
        try:
            pw.stop()
        except Exception:
            pass


def search_mrt_products(keyword: str, top_n: int = 3) -> list[dict]:
    """키워드로 MRT 상품을 검색하고 제휴 링크를 붙여 반환한다.

    Returns: [{"name": str, "url": str, "price": str, "image": str}, ...] (url은 제휴 링크, image는 원본 썸네일 URL)
    """
    api_key = os.environ.get("MRT_API_KEY", "").strip()
    if not api_key:
        return []

    products = _mrt_search_products(keyword, top_n=top_n)
    results = []
    for p in products:
        affiliate_url = create_mrt_affiliate_link(p["url"])
        if not affiliate_url:
            continue
        results.append({
            "name": p["title"], "url": affiliate_url, "price": p.get("price", ""),
            "image": p.get("image", ""),
        })
    return results


def download_product_image(image_url: str, dest_dir: str, filename_hint: str) -> str:
    """상품 썸네일 이미지를 로컬에 저장하고 파일 경로를 반환한다. 실패 시 빈 문자열."""
    if not image_url:
        return ""
    try:
        import os as _os
        import re as _re
        _os.makedirs(dest_dir, exist_ok=True)
        ext = ".jpg"
        m = _re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", image_url.lower())
        if m:
            ext = "." + m.group(1)
        safe_name = _re.sub(r"[^\w\-]", "_", filename_hint)[:60] or "image"
        dest_path = _os.path.join(dest_dir, f"{safe_name}{ext}")
        req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        with open(dest_path, "wb") as f:
            f.write(data)
        return dest_path
    except Exception:
        return ""


def get_mrt_affiliate_block(keyword: str) -> str:
    """키워드로 여행 투어/티켓을 검색해 마크다운 링크 블록을 반환한다.

    키가 없거나 검색 결과가 없으면 빈 문자열을 반환한다 (발행 중단 없음).
    """
    if not os.environ.get("MRT_API_KEY", "").strip():
        return ""

    try:
        products = search_mrt_products(keyword, top_n=2)
        if not products:
            return ""

        lines = ["\n\n---\n\n### 함께 예약하면 좋은 투어·티켓\n"]
        for p in products:
            lines.append(f"- [{_sanitize_link_text(p['name'])}]({p['url']})")
        lines.append(f"\n*{MRT_DISCLOSURE}*\n")
        return "\n".join(lines)
    except Exception:
        return ""


# ─── 제휴상품 탭 (검색·선택 → compose panel 핸드오프) ─────────────────────

SOURCE_LABELS = {"coupang": "쿠팡파트너스", "mrt": "마이리얼트립"}


def keys_configured(source: str) -> bool:
    if source == "coupang":
        access_key, secret_key = _coupang_keys()
        return bool(access_key and secret_key)
    if source == "mrt":
        return bool(os.environ.get("MRT_API_KEY", "").strip())
    return False


def search_products_for_tab(source: str, keyword: str, limit: int = 8) -> list[dict]:
    """제휴상품 탭용 통합 검색. 실제 제휴 링크로 변환된 결과를 반환한다.

    Returns: [{"name": str, "url": str, "price": str, "source": str}, ...]
    """
    if source == "coupang":
        raw = search_coupang_products(keyword, limit=limit)
        results = []
        for p in raw:
            link = create_coupang_affiliate_link(p["url"]) if p.get("url") else ""
            if not link:
                continue
            results.append({"name": p["name"], "url": link, "price": "", "source": "coupang"})
        return results
    if source == "mrt":
        raw = search_mrt_products(keyword, top_n=limit)
        return [{**p, "source": "mrt"} for p in raw]
    return []


def format_product_context(product: dict) -> str:
    """선택한 제휴 상품 정보를 compose panel의 공식 근거 자료 칸에 넣을 텍스트로 변환한다.

    프롬프트가 이 상품을 중심으로 한 글을 쓰도록 상품정보 + 링크 삽입 지시를 담는다.
    수수료 고지문은 AI가 텍스트로 쓰지 않는다 — get_disclosure_banner()가 만드는
    이미지를 발행 시 본문 맨 앞에 붙이는 방식으로 대신한다 (AI가 위치를 제멋대로
    고르는 문제를 없애기 위함).
    """
    source = product.get("source", "")
    label = SOURCE_LABELS.get(source, source)

    lines = [f"[{label} 제휴 상품 정보]"]
    lines.append(f"상품명: {clean_product_name(product.get('name', ''))}")
    if product.get("price"):
        lines.append(f"가격: {product['price']}")
    lines.append(f"제휴 링크: {product.get('url', '')}")
    lines.append("")
    lines.append("[링크 삽입 지시]")
    lines.append(f"위 제휴 링크를 본문 안에 자연스러운 문구로 최소 1회 마크다운 링크 [문구]({product.get('url', '')}) 형식으로 삽입할 것.")
    lines.append("수수료 고지 문구는 본문에 직접 쓰지 말 것 (별도 이미지로 자동 첨부됨).")
    return "\n".join(lines)


def get_disclosure_banner(source: str) -> str:
    """쿠팡/MRT 수수료 고지 배너 이미지를 생성한다. 이미 있으면 재생성하지 않고 그대로 재사용한다.

    발행 시 image_paths의 맨 앞에 넣으면 본문 최상단(가장 먼저 배치되는 이미지)에
    노출된다. Pillow가 없거나 실패하면 빈 문자열을 반환한다 (발행 중단 없음).
    """
    from config import IMAGES_DIR

    disclosure = COUPANG_DISCLOSURE if source == "coupang" else MRT_DISCLOSURE
    output_dir = IMAGES_DIR / "disclosure"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{source}_disclosure.jpg"
    if path.exists():
        return str(path)

    try:
        from PIL import Image, ImageDraw, ImageFont
        from media.cards import _font, _wrap

        # 브랜드별 톤 — 쿠팡은 붉은 계열, 마이리얼트립은 짙은 주황 계열
        if source == "coupang":
            accent = (176, 46, 38)
            pill_bg = (253, 226, 224)
            logo_path = None
        else:
            accent = (176, 90, 20)
            pill_bg = (252, 224, 194)
            from config import BASE_DIR
            logo_path = BASE_DIR / "images" / "brand" / "myrealtrip_logo.png"

        w = 420
        card_bg = (255, 255, 255)
        card_border = (228, 231, 236)
        text_color = (95, 100, 110)

        font_pill = _font(ImageFont, 17)
        font_body = _font(ImageFont, 18)

        pill_label = "Partners"
        icon_size = 16
        pill_h = 30
        gap = 8
        pad_x = 14
        pad_y = 10

        body_lines = _wrap(disclosure, 26)[:2]
        body_line_h = int(18 * 1.45)
        card_h = pad_y * 2 + body_line_h * len(body_lines)
        h = pill_h + gap + card_h

        img = Image.new("RGB", (w, h), (255, 255, 255))
        draw = ImageDraw.Draw(img)

        # 상단 작은 배지: 아이콘 + "Partners" (라운딩을 배지 자체 높이 기준으로만 적용해 잘림 방지)
        logo_img = None
        if logo_path and logo_path.exists():
            try:
                logo_img = Image.open(logo_path).convert("RGBA").resize((icon_size, icon_size))
            except Exception:
                logo_img = None

        pill_text = pill_label
        ptbbox = draw.textbbox((0, 0), pill_text, font=font_pill)
        pill_content_w = icon_size + 6 + (ptbbox[2] - ptbbox[0])
        pill_w = pill_content_w + 22
        pill_x = (w - pill_w) // 2
        pill_radius = 10
        draw.rounded_rectangle((pill_x, 0, pill_x + pill_w, pill_h), radius=pill_radius, fill=pill_bg)

        icon_x = pill_x + 11
        icon_y = (pill_h - icon_size) // 2
        if logo_img is not None:
            img.paste(logo_img, (icon_x, icon_y), logo_img)
        else:
            draw.ellipse((icon_x, icon_y, icon_x + icon_size, icon_y + icon_size), outline=accent, width=2)
            cbbox = draw.textbbox((0, 0), "✓", font=font_pill)
            draw.text(
                (icon_x + (icon_size - (cbbox[2] - cbbox[0])) / 2, icon_y + (icon_size - (cbbox[3] - cbbox[1])) / 2 - cbbox[1]),
                "✓", font=font_pill, fill=accent,
            )
        draw.text((icon_x + icon_size + 6, (pill_h - (ptbbox[3] - ptbbox[1])) / 2 - ptbbox[1]), pill_text, font=font_pill, fill=accent)

        # 하단 카드: 고지 문구 (완만한 라운딩)
        card_top = pill_h + gap
        draw.rounded_rectangle((0, card_top, w - 1, card_top + card_h - 1), radius=12, fill=card_bg, outline=card_border, width=2)
        y = card_top + pad_y
        for line in body_lines:
            lbbox = draw.textbbox((0, 0), line, font=font_body)
            x = (w - (lbbox[2] - lbbox[0])) // 2
            draw.text((x, y), line, font=font_body, fill=text_color)
            y += body_line_h

        img.save(path, "JPEG", quality=92, optimize=True)
        return str(path)
    except Exception:
        return ""


# ─── 파이프라인 연결 ───────────────────────────────────────────────────────

def attach_affiliate_block(blog_type: str, keyword: str, body: str) -> str:
    """blog_type에 맞는 제휴 블록을 생성해 본문 끝에 붙인다.

    여행 글이면 MRT, 그 외 실패/미지원이면 쿠팡을 시도한다. 실패해도 원본
    body를 그대로 반환한다 (발행 자체는 절대 막지 않는다).
    """
    block = ""
    try:
        if blog_type == "여행":
            block = get_mrt_affiliate_block(keyword)
        if not block:
            block = get_coupang_affiliate_block(keyword, blog_type)
    except Exception:
        block = ""

    if not block:
        return body
    return body.rstrip() + "\n" + block


def with_disclosure_banner(blog_type: str, image_paths: list) -> list:
    """blog_type에 맞는 고지 배너 이미지를 image_paths에 자연스러운 위치로 끼워 넣는다.

    티스토리/워드프레스용 — 네이버처럼 별도 템플릿 기능이 없어 이미지로 대신한다.
    이미지가 이미 있으면 두 번째 자리에 넣어(워드프레스 대표 이미지는 그대로 유지),
    없으면 맨 앞에 넣는다. 매핑된 유형이 없거나 배너 생성 실패 시 원본 그대로 반환한다.
    """
    source = DISCLOSURE_SOURCE_MAP.get(blog_type)
    if not source:
        return image_paths
    banner = get_disclosure_banner(source)
    if not banner:
        return image_paths
    paths = list(image_paths)
    insert_at = 1 if paths else 0
    paths.insert(insert_at, banner)
    return paths
