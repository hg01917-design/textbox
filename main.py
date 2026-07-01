from __future__ import annotations

import argparse
from datetime import datetime

from config import get_blog_profile, load_env
from content.generator import generate_draft_with_sources
from content.official_url import find_official_urls
from content.prompting import prompt_for_blog_type, prompt_names
from content.public_sources import fetch_public_source_context
from content.quality import check_draft
from content.source import fetch_sources, format_sources_for_prompt
from keywords.analyzer import analyze_keyword
from media.cards import generate_card_images
from publisher.wordpress import create_post
from storage.drafts import save_draft


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Blog draft generator")
    parser.add_argument("--keyword", required=True, help="Seed keyword")
    parser.add_argument("--blog-type", default="일반", help="정부지원, 여행, IT, 생활정보, 일반")
    parser.add_argument("--limit", type=int, default=15, help="Related keyword candidate limit")
    parser.add_argument("--min-chars", type=int, default=1200, help="Minimum body chars for quality check")
    parser.add_argument(
        "--source-url",
        action="append",
        default=[],
        help="Official source URL to ground the draft. Can be used multiple times.",
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "cli", "openai", "template"],
        default="auto",
        help="Draft generator provider",
    )
    parser.add_argument("--wp-draft", action="store_true", help="Save passing draft to WordPress as draft")
    parser.add_argument("--wp-publish", action="store_true", help="Publish passing draft to WordPress")
    parser.add_argument("--wp-category", default="", help="WordPress category name")
    parser.add_argument("--prompt", default="", help="Prompt file name in prompts/")
    parser.add_argument("--cards", action="store_true", help="Generate card images and attach them")
    return parser


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    profile = get_blog_profile(args.blog_type)

    print(f"[1/4] 키워드 분석: {args.keyword}")
    analysis = analyze_keyword(
        args.keyword,
        max_competition=profile["max_competition"],
        limit=args.limit,
    )
    best_keyword = analysis.get("best_keyword") or args.keyword
    related = [row["keyword"] for row in analysis.get("candidates", []) if row["keyword"] != best_keyword]

    sources = []
    source_context = ""
    source_urls = _merge_urls(args.source_url, find_official_urls(best_keyword, args.blog_type, on_log=print))
    if source_urls:
        print(f"[2/5] 공식 URL 읽기: {len(source_urls)}개")
        sources = fetch_sources(source_urls)
        source_context = format_sources_for_prompt(sources)
    public_context = fetch_public_source_context(best_keyword, args.blog_type, on_log=print)
    if public_context:
        print("[2/5] 공공API 자료 보강 완료")
        source_context = "\n\n".join(part for part in (source_context, public_context) if part)
        sources.append({"url": "public-api:data.go.kr", "ok": True, "error": "", "text": public_context})

    has_sources = bool(source_urls or public_context)
    step_prefix = "[3/5]" if has_sources else "[2/4]"
    print(f"{step_prefix} 초안 생성: {best_keyword}")
    draft = generate_draft_with_sources(
        best_keyword,
        args.blog_type,
        related,
        provider=args.provider,
        source_context=source_context,
        prompt_name=args.prompt or prompt_for_blog_type(args.blog_type),
    )

    images = []
    if args.cards:
        print("[cards] 카드 이미지 생성")
        images = generate_card_images(best_keyword, args.blog_type, on_log=print)

    print("[4/5] 품질 검사" if has_sources else "[3/4] 품질 검사")
    quality = check_draft(draft, best_keyword, min_chars=args.min_chars)

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "keyword": best_keyword,
        "seed_keyword": args.keyword,
        "blog_type": args.blog_type,
        "analysis": analysis,
        "draft": draft,
        "quality": quality,
        "source_urls": source_urls + (["public-api:data.go.kr"] if public_context else []),
        "sources": sources,
        "images": images,
    }

    print("[5/5] 파일 저장" if has_sources else "[4/4] 파일 저장")
    paths = save_draft(payload)
    print(f"Markdown: {paths['markdown']}")
    print(f"JSON: {paths['json']}")
    print(f"Quality: {'PASS' if quality['passed'] else 'WARN'} {quality['warnings']}")
    if args.wp_draft or args.wp_publish:
        if not quality["passed"]:
            print("WordPress: skipped because quality check did not pass")
        else:
            status = "publish" if args.wp_publish else "draft"
            print(f"WordPress: saving as {status}")
            result = create_post(
                title=draft["title"],
                content_markdown=draft["body"],
                tags=draft.get("tags", []),
                status=status,
                category=args.wp_category,
                image_paths=images,
            )
            if result.get("ok"):
                print(f"WordPress: OK id={result.get('id')} status={result.get('status')} link={result.get('link')}")
            else:
                print(f"WordPress: ERROR {result.get('error')}")
    return 0


def _merge_urls(primary: list[str], secondary: list[str]) -> list[str]:
    merged = []
    for url in primary + secondary:
        if url and url not in merged:
            merged.append(url)
    return merged


if __name__ == "__main__":
    raise SystemExit(main())
