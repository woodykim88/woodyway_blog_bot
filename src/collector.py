"""GeekNews Atom 피드에서 주제 후보를 수집한다."""
from __future__ import annotations

import html
import re
from calendar import timegm
from datetime import datetime, timedelta, timezone

import feedparser

from src.http_client import HttpGet
from src.models import Candidate

GEEKNEWS_FEED_URL = 'https://news.hada.io/rss/news'
DEFAULT_MAX_AGE_HOURS = 48
DEFAULT_LIMIT = 30
# 원문 본문을 추출할 수 없거나(영상) 외부 원문이 없는(Ask GN) 글은 선별 전에 제외
EXCLUDED_TITLE_MARKERS = ('[영상]', '[동영상]', '[팟캐스트]', 'Ask GN')

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
    limit: int = DEFAULT_LIMIT,
) -> list[Candidate]:
    """피드 원문을 파싱해 최근 max_age_hours 이내 항목을 최신순으로 최대 limit개 반환합니다."""
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
            geeknews_url=entry.link,
            summary=_entry_summary(entry)[:600],
            published=published,
        ))
    return sorted(candidates, key=lambda c: c.published, reverse=True)[:limit]


def fetch_candidates(get: HttpGet, now: datetime) -> list[Candidate]:
    return parse_feed(get(GEEKNEWS_FEED_URL), now)
