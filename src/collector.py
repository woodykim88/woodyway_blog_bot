"""GeekNews와 국내 IT 언론사 RSS 피드에서 주제 후보를 수집한다.

네이버 IT 뉴스(news.naver.com)는 robots.txt로 모든 봇 접근과 AI 활용을 금지하므로 긁지 않는다.
대신 네이버에 올라오는 같은 기사를 언론사가 공식 RSS로 배포하는 곳에서 직접 받는다.
"""
from __future__ import annotations

import html
import re
from calendar import timegm
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from src.http_client import HttpGet
from src.models import Candidate

GEEKNEWS_FEED_URL = 'https://news.hada.io/rss/news'
DEFAULT_MAX_AGE_HOURS = 48
DEFAULT_LIMIT = 30
# 원문 본문을 추출할 수 없거나(영상·사진) 외부 원문이 없거나(Ask GN) 글감이 아닌(인사·부고) 글은 선별 전에 제외
EXCLUDED_TITLE_MARKERS = (
    '[영상]', '[동영상]', '[팟캐스트]', 'Ask GN', '[포토]', '[사진]', '[인사]', '[부고]', '[게시판]',
)


@dataclass(frozen=True)
class FeedSource:
    name: str     # 글의 출처 표시와 선별 프롬프트에 쓰는 이름
    url: str
    direct: bool  # True면 피드 링크가 곧 원문 기사
    limit: int    # 이 피드에서 가져올 최신 후보 수


GEEKNEWS = FeedSource('GeekNews', GEEKNEWS_FEED_URL, direct=False, limit=20)
NEWS_SOURCES = (
    FeedSource('한국경제', 'https://www.hankyung.com/feed/it', direct=True, limit=10),
    FeedSource('ZDNet Korea', 'https://feeds.feedburner.com/zdkorea', direct=True, limit=10),
    FeedSource('AI타임스', 'https://www.aitimes.com/rss/allArticle.xml', direct=True, limit=10),
    FeedSource('디지털투데이', 'https://www.digitaltoday.co.kr/rss/allArticle.xml', direct=True, limit=10),
)
DEFAULT_SOURCES = (GEEKNEWS, *NEWS_SOURCES)

_TAG_RE = re.compile(r'<[^>]+>')
_SPACE_RE = re.compile(r'\s+')


def html_to_text(value: str) -> str:
    """HTML 태그를 지우고 공백을 정리한 평문을 반환합니다."""
    return _SPACE_RE.sub(' ', html.unescape(_TAG_RE.sub(' ', value))).strip()


def _entry_summary(entry: feedparser.FeedParserDict) -> str:
    if entry.get('content'):
        return html_to_text(entry.content[0].value)
    return html_to_text(entry.get('summary', ''))


def parse_feed(
    raw: str,
    now: datetime,
    max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
    source: FeedSource = GEEKNEWS,
) -> list[Candidate]:
    """피드 원문을 파싱해 최근 max_age_hours 이내 항목을 최신순으로 최대 source.limit개 반환합니다."""
    cutoff = now - timedelta(hours=max_age_hours)
    candidates = []
    for entry in feedparser.parse(raw).entries:
        stamp = entry.get('published_parsed') or entry.get('updated_parsed')
        if not stamp or not entry.get('title') or not entry.get('link'):
            continue
        if any(marker in entry.title for marker in EXCLUDED_TITLE_MARKERS):
            continue
        published = datetime.fromtimestamp(timegm(stamp), tz=timezone.utc)
        if published < cutoff:
            continue
        candidates.append(Candidate(
            title=entry.title.strip(),
            link=entry.link,
            summary=_entry_summary(entry)[:600],
            published=published,
            source=source.name,
            direct=source.direct,
        ))
    return sorted(candidates, key=lambda c: c.published, reverse=True)[:source.limit]


def fetch_candidates(
    get: HttpGet, now: datetime, sources: tuple[FeedSource, ...] = DEFAULT_SOURCES
) -> list[Candidate]:
    """출처 순서대로 후보를 이어 붙입니다. 피드 하나가 실패해도 나머지로 계속합니다."""
    candidates: list[Candidate] = []
    for source in sources:
        try:
            raw = get(source.url)
        except requests.RequestException as e:
            print(f'[수집 실패] {source.name}: {e.__class__.__name__}')
            continue
        candidates.extend(parse_feed(raw, now, source=source))
    return candidates
