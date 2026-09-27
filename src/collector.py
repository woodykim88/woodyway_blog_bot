"""GeekNews·국내 IT 언론사와 생활경제 언론사 RSS, 정책브리핑 목록에서 주제 후보를 수집한다.

네이버 IT 뉴스(news.naver.com)는 robots.txt로 모든 봇 접근과 AI 활용을 금지하므로 긁지 않는다.
대신 네이버에 올라오는 같은 기사를 언론사가 공식 RSS로 배포하는 곳에서 직접 받는다.
robots.txt로 AI 수집·활용을 막은 매체(서울경제, 연합뉴스, 조선비즈, 머니투데이 등)는 쓰지 않는다 (2026-09-27 확인).
"""
from __future__ import annotations

import html
import re
from calendar import timegm
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

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
    kind: str = 'rss'  # 'rss' 또는 'policy'(정책브리핑 목록 페이지, RSS가 없어 목록을 읽는다)


GEEKNEWS = FeedSource('GeekNews', GEEKNEWS_FEED_URL, direct=False, limit=20)
NEWS_SOURCES = (
    FeedSource('한국경제', 'https://www.hankyung.com/feed/it', direct=True, limit=10),
    FeedSource('ZDNet Korea', 'https://feeds.feedburner.com/zdkorea', direct=True, limit=10),
    FeedSource('AI타임스', 'https://www.aitimes.com/rss/allArticle.xml', direct=True, limit=10),
    FeedSource('디지털투데이', 'https://www.digitaltoday.co.kr/rss/allArticle.xml', direct=True, limit=10),
)
DEFAULT_SOURCES = (GEEKNEWS, *NEWS_SOURCES)
POLICY_LIST_URL = 'https://www.korea.kr/news/policyNewsList.do?pageIndex=1'
POLICY_VIEW_URL = 'https://www.korea.kr/news/policyNewsView.do?newsId={doc_id}'
MONEY_SOURCES = (
    FeedSource('한국경제', 'https://www.hankyung.com/feed/economy', direct=True, limit=10),
    FeedSource('한국경제', 'https://www.hankyung.com/feed/realestate', direct=True, limit=6),
    FeedSource('이투데이', 'https://rss.etoday.co.kr/eto/economy_news.xml', direct=True, limit=8),
    FeedSource('이투데이', 'https://rss.etoday.co.kr/eto/finance_news.xml', direct=True, limit=8),
    FeedSource('파이낸셜뉴스', 'https://www.fnnews.com/rss/r20/fn_realnews_economy.xml', direct=True, limit=5),
    FeedSource('파이낸셜뉴스', 'https://www.fnnews.com/rss/r20/fn_realnews_finance.xml', direct=True, limit=5),
    FeedSource('파이낸셜뉴스', 'https://www.fnnews.com/rss/r20/fn_realnews_realestate.xml', direct=True, limit=5),
    FeedSource('정책브리핑', POLICY_LIST_URL, direct=True, limit=15, kind='policy'),  # 공공누리 제1유형
)
POLICY_MAX_AGE_DAYS = 4  # 목록에는 날짜만 있어 날짜로 비교한다 (생활경제 인스타 채널과 같은 기준)
# 생활경제와 거리가 먼 정책 기사 (선별 LLM 전에 걸러 비용 절약)
POLICY_EXCLUDED_WORDS = ('정상', '외교', '국방', '군사', '유엔', '총리 주재', '훈장', '추모', '기념식', '인사 발령',
                         '대통령', '안보', '북한')
_POLICY_ITEM_RE = re.compile(r'policyNewsView\.do\?newsId=(\d+).*?<strong>(.*?)</strong>(.*?)</a>', re.S)
_POLICY_DATE_RE = re.compile(r'(\d{4})\.(\d{2})\.(\d{2})')

_TAG_RE = re.compile(r'<[^>]+>')
_SPACE_RE = re.compile(r'\s+')


def html_to_text(value: str) -> str:
    """HTML 태그를 지우고 공백을 정리한 평문을 반환합니다."""
    return _SPACE_RE.sub(' ', html.unescape(_TAG_RE.sub(' ', value))).strip()


def _entry_summary(entry: feedparser.FeedParserDict) -> str:
    if entry.get('content'):
        return html_to_text(entry.content[0].value)
    return html_to_text(entry.get('summary', ''))


def _published(entry: feedparser.FeedParserDict) -> datetime | None:
    """발행 시각. 'Sun,27 Sep 2026'처럼 쉼표 뒤 공백이 없어 feedparser가 못 읽는 날짜도 직접 읽는다."""
    stamp = entry.get('published_parsed') or entry.get('updated_parsed')
    if stamp:
        return datetime.fromtimestamp(timegm(stamp), tz=timezone.utc)
    raw = (entry.get('published') or entry.get('updated') or '').strip()
    try:
        return parsedate_to_datetime(re.sub(r',(?=\S)', ', ', raw)).astimezone(timezone.utc) if raw else None
    except (TypeError, ValueError):
        return None


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
        published = _published(entry)
        if published is None or not entry.get('title') or not entry.get('link'):
            continue
        if any(marker in entry.title for marker in EXCLUDED_TITLE_MARKERS):
            continue
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


def parse_policy_list(
    page_html: str, now: datetime, limit: int = 15, max_age_days: int = POLICY_MAX_AGE_DAYS
) -> list[Candidate]:
    """정책브리핑 정책뉴스 목록 → 후보. 기사 텍스트는 공공누리 제1유형(출처 표시)이라 글감으로 쓸 수 있다."""
    kst = timezone(timedelta(hours=9))
    cutoff = (now.astimezone(kst) - timedelta(days=max_age_days)).date()
    seen: set[str] = set()
    items = []
    for match in _POLICY_ITEM_RE.finditer(page_html):
        doc_id, title_html, rest = match.groups()
        title, date = html_to_text(title_html), _POLICY_DATE_RE.search(rest)
        if doc_id in seen or not title or not date:
            continue
        seen.add(doc_id)
        published = datetime(*map(int, date.groups()), tzinfo=kst)
        if published.date() < cutoff or any(w in title for w in POLICY_EXCLUDED_WORDS):
            continue
        summary = html_to_text(_POLICY_DATE_RE.sub('', rest))[:400]
        items.append(Candidate(title=title, link=POLICY_VIEW_URL.format(doc_id=doc_id), summary=summary,
                               published=published, source='정책브리핑', direct=True))
    return items[:limit]


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
        if source.kind == 'policy':
            candidates.extend(parse_policy_list(raw, now, source.limit))
        else:
            candidates.extend(parse_feed(raw, now, source=source))
    return candidates
