"""pages/*.html을 Blogger 고정 페이지로 올리거나 고친다 (2026-10-22, 테마 푸터 링크용).

주소는 처음 발행할 때의 제목으로 정해지므로 영어 이름(about 등)으로 발행해 /p/about.html을 만든 뒤
제목만 한국어로 바꾼다. 이미 있는 페이지(같은 주소)는 내용과 제목만 고친다.

사용법: .venv/bin/python scripts/publish_pages.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auto_blogger  # noqa: E402

PAGES = {  # 파일 이름(=주소) → 보이는 제목
    'about': '운영자 소개',
    'contact': '문의',
    'privacy-policy': '개인정보처리방침',
}


def existing_pages(service, blog_id: str) -> dict[str, dict]:
    items = auto_blogger.execute_with_retry(service.pages().list(blogId=blog_id, status=['LIVE', 'DRAFT'])).get('items', [])
    return {Path(p.get('url', '')).stem: p for p in items if p.get('url')}


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    config = auto_blogger.load_config(os.environ)
    service = auto_blogger.get_blogger_service(config)
    found = existing_pages(service, config.blog_id)
    for slug, title in PAGES.items():
        content = (ROOT / 'pages' / f'{slug}.html').read_text(encoding='utf-8')
        page = found.get(slug)
        if page is None:
            page = auto_blogger.execute_with_retry(service.pages().insert(
                blogId=config.blog_id, body={'title': slug, 'content': content}, isDraft=False))
            print(f'[새 페이지] {page.get("url")}')
        page = auto_blogger.execute_with_retry(service.pages().patch(
            blogId=config.blog_id, pageId=page['id'], body={'title': title, 'content': content}))
        print(f'[반영] {title} → {page.get("url")}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
