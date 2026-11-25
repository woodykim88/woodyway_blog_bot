"""지금 한국에서 많이 검색되는 키워드를 모아 선별 모델에 알려 준다 (인기 주제를 싸게 가려내는 신호).

키워드만 쓰고 기사 본문은 가져오지 않는다. 어느 한쪽이 실패해도 발행은 계속된다 (신호가 없을 뿐).
- 구글 트렌드 한국 일간 RSS: robots.txt가 /trending/rss를 막지 않는다 (2026-09-30 확인)
- whereispost.com/hot: 포털 급상승 키워드. robots.txt에 금지 규칙이 없다 (2026-09-30 확인, 운영자가 알려 준 출처)
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

from src.http_client import HttpGet

GOOGLE_TRENDS_URL = 'https://trends.google.com/trending/rss?geo=KR&hl=ko'
WHEREISPOST_URL = 'https://whereispost.com/hot'
MAX_PER_SOURCE = 20
MAX_KEYWORD_CHARS = 30  # 외부 페이지에서 온 글자라 프롬프트에 넣기 전에 길이를 자르고 줄바꿈을 없앤다
_HANGUL_RE = re.compile(r'[가-힣]')
_RANK_TEXT_RE = re.compile(r'<span class="rank-txt">(.*?)</span>', re.S)


def _clean(keyword: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(keyword)).strip()[:MAX_KEYWORD_CHARS]


def parse_google_trends(rss: str) -> tuple[str, ...]:
    """RSS의 검색어 중 한글이 든 것만 (영어·중국어 검색어는 국내 글감 신호로 쓰기 어렵다)."""
    titles = (item.findtext('title') or '' for item in ET.fromstring(rss).iter('item'))
    return tuple(_clean(t) for t in titles if _HANGUL_RE.search(t))[:MAX_PER_SOURCE]


def parse_whereispost(page: str) -> tuple[str, ...]:
    keywords = (_clean(m) for m in _RANK_TEXT_RE.findall(page))
    return tuple(k for k in keywords if k)[:MAX_PER_SOURCE]


MIN_MATCH_CHARS = 2  # 한 글자 키워드는 아무 제목에나 들어가 일치로 치지 않는다


def _squash(text: str) -> str:
    return re.sub(r'\s+', '', text).lower()


def match_trending(title: str, trending: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """제목에 그대로 들어 있는 인기 키워드. 모델의 재량이 아니라 코드로 먼저 가려 둔다 (띄어쓰기 차이는 무시)."""
    squashed = _squash(title)
    return tuple(k for k in trending if len(_squash(k)) >= MIN_MATCH_CHARS and _squash(k) in squashed)


def fetch_trending(get: HttpGet) -> tuple[str, ...]:
    """두 출처의 인기 키워드를 순서대로 합칩니다 (중복 제거). 실패한 출처는 건너뜁니다."""
    keywords: list[str] = []
    for name, url, parse in (('구글 트렌드', GOOGLE_TRENDS_URL, parse_google_trends),
                             ('실시간 검색어', WHEREISPOST_URL, parse_whereispost)):
        try:
            found = parse(get(url))
        except (requests.RequestException, ET.ParseError) as e:
            print(f'[트렌드 건너뜀] {name}: {e.__class__.__name__}')
            continue
        keywords.extend(k for k in found if k not in keywords)
    return tuple(keywords)


# --- 화제 우선 글감용 (2026-11-26 사용자 결정: 뉴스 랭킹·급상승 검색어에 무게) ---
HT_NS = '{https://trends.google.com/trending/rss}'


@dataclass(frozen=True)
class NewsLink:
    title: str
    url: str
    source: str


@dataclass(frozen=True)
class TrendItem:
    """구글 트렌드 급상승 검색어 하나와 구글이 함께 묶어 둔 기사들."""

    keyword: str
    traffic: int  # 대략 검색량 ("2,000+" → 2000)
    news: tuple[NewsLink, ...] = ()


def parse_traffic(text: str) -> int:
    digits = re.sub(r'[^0-9]', '', text or '')
    return int(digits) if digits else 0


def _news_links(item: ET.Element) -> tuple[NewsLink, ...]:
    links = []
    for news in item.iter(f'{HT_NS}news_item'):
        url = (news.findtext(f'{HT_NS}news_item_url') or '').strip()
        if url.startswith('http'):
            links.append(NewsLink(_clean(news.findtext(f'{HT_NS}news_item_title') or ''), url,
                                  _clean(news.findtext(f'{HT_NS}news_item_source') or '')))
    return tuple(links)


def parse_google_trend_items(rss: str) -> tuple[TrendItem, ...]:
    """한글 검색어만, 검색량·기사와 함께 (RSS 순서 유지)."""
    items = []
    for item in ET.fromstring(rss).iter('item'):
        keyword = _clean(item.findtext('title') or '')
        if _HANGUL_RE.search(keyword):
            items.append(TrendItem(keyword, parse_traffic(item.findtext(f'{HT_NS}approx_traffic') or ''), _news_links(item)))
    return tuple(items)[:MAX_PER_SOURCE]


def fetch_trend_items(get: HttpGet) -> tuple[TrendItem, ...]:
    """구글 트렌드 급상승 검색어(기사 포함) 뒤에 포털 급상승 검색어(기사 없음)를 붙인다. 실패한 출처는 건너뛴다."""
    items: list[TrendItem] = []
    try:
        items.extend(parse_google_trend_items(get(GOOGLE_TRENDS_URL)))
    except (requests.RequestException, ET.ParseError) as e:
        print(f'[트렌드 건너뜀] 구글 트렌드: {e.__class__.__name__}')
    try:
        seen = {i.keyword for i in items}
        items.extend(TrendItem(k, 0) for k in parse_whereispost(get(WHEREISPOST_URL)) if k not in seen)
    except requests.RequestException as e:
        print(f'[트렌드 건너뜀] 실시간 검색어: {e.__class__.__name__}')
    return tuple(items)
