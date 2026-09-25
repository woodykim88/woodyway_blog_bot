"""GeekNews 토픽 페이지에서 원문 링크를 찾고, 원문 본문을 추출한다."""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests
import trafilatura

from src.http_client import HttpGet
from src.models import Article, Candidate

MIN_ARTICLE_CHARS = 500
MAX_ARTICLE_CHARS = 12000
GEEKNEWS_HOST = 'news.hada.io'


class _TitleLinkParser(HTMLParser):
    """class에 topic-title-link가 있는 첫 <a>의 href를 찾는다."""

    def __init__(self) -> None:
        super().__init__()
        self.href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != 'a' or self.href is not None:
            return
        attr_map = dict(attrs)
        if 'topic-title-link' in (attr_map.get('class') or '').split():
            self.href = attr_map.get('href')


def extract_original_url(topic_html: str, base_url: str) -> str | None:
    """토픽 페이지의 제목 링크(원문)를 반환합니다. Ask/Show GN처럼 원문이 GeekNews 자체면 None."""
    parser = _TitleLinkParser()
    parser.feed(topic_html)
    if not parser.href:
        return None
    url = urljoin(base_url, parser.href)
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or parsed.netloc.endswith(GEEKNEWS_HOST):
        return None
    return url


def extract_article_text(page_html: str, url: str) -> str | None:
    """원문 HTML에서 본문만 추출합니다. 너무 짧으면(페이월, 동영상 등) None."""
    text = trafilatura.extract(page_html, url=url, include_comments=False, include_tables=False)
    if not text or len(text) < MIN_ARTICLE_CHARS:
        return None
    return text[:MAX_ARTICLE_CHARS]


def fetch_article(get: HttpGet, candidate: Candidate) -> Article | None:
    """후보의 원문을 가져옵니다. 실패하면 이유를 출력하고 None을 반환합니다."""
    try:
        source_url = extract_original_url(get(candidate.geeknews_url), candidate.geeknews_url)
        if source_url is None:
            print(f'[건너뜀] 외부 원문 링크 없음: {candidate.title}')
            return None
        text = extract_article_text(get(source_url), source_url)
    except requests.RequestException as e:
        print(f'[건너뜀] 원문 요청 실패 ({e.__class__.__name__}): {candidate.title}')
        return None
    if text is None:
        print(f'[건너뜀] 원문 본문 추출 실패/너무 짧음: {candidate.title}')
        return None
    return Article(candidate=candidate, source_url=source_url, text=text)
