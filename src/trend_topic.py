"""화제 우선 글감 (2026-11-26 사용자 결정: 뉴스 랭킹·급상승 검색어에 무게를 둬야 검색에 걸린다).

흐름: 구글 트렌드 급상승 검색어 → 모델이 이 블로그(내 돈·생활) 독자에게 쓸모 있는 검색어만 채점
→ 그 검색어로 구글이 묶어 둔 기사 + 경제 언론사 RSS에서 같은 검색어가 든 기사를 모은다.
글 제목에는 급상승 검색어를 그대로 넣는다 (검색에 걸리게 하는 핵심).

기사 주소는 매체가 여러 곳이라, robots.txt가 AI 수집(GPTBot)이나 전체 봇을 막은 매체는 읽지 않는다
(collector.py와 같은 원칙). robots.txt를 못 읽은 매체도 건너뛴다.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import parse_qs, quote_plus, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from pydantic import BaseModel, Field

from src import trends
from src.http_client import HttpGet
from src.llm import LLMClient, load_prompt
from src.models import Candidate
from src.trends import NewsLink, TrendItem

MIN_TREND_SCORE = 7  # 이 점수 미만이면 화제 글을 쓰지 않고 생활 가이드로 넘어간다
MAX_PICKS = 3        # 원문을 못 구하면 다음 검색어로 넘어가는 횟수
MAX_SOURCES = 3      # 한 글에 함께 읽히는 기사 수 (1건 재작성 방지)
AI_AGENTS = ('GPTBot', '*')
# 구글 트렌드 RSS는 상위 10개 남짓에만 기사가 붙고, 포털 급상승 검색어엔 기사가 없다 → 검색어로 뉴스를 찾는다.
# bing.com robots.txt는 /news/search를 막지 않는다 (2026-11-26 확인). 결과 링크는 apiclick 주소라 url= 값을 꺼낸다.
BING_NEWS_URL = 'https://www.bing.com/news/search?q={q}&format=rss&setlang=ko&cc=KR'
SKIP_HOSTS = ('msn.com',)  # 언론사 기사 재배포본 (원문 매체 주소가 아니다)
GENERIC_WORDS = frozenset({'대책', '피해', '조사', '추가', '발표', '논란', '방법', '신청', '조회', '오늘', '관련'})
MIN_SHARED_TOKENS = 2


class TrendChoice(BaseModel):
    index: int
    score: int = Field(ge=1, le=10)
    reason: str
    search_query: str
    angle: str


class TrendSelection(BaseModel):
    choices: list[TrendChoice]


@dataclass(frozen=True)
class TrendPick:
    item: TrendItem
    score: int
    search_query: str  # 글 제목·메타 설명에 그대로 넣을 검색어 (= 급상승 검색어)
    angle: str         # 독자가 얻는 것 (조회 방법, 내 돈 영향, 할 일)


def format_items(items: Sequence[TrendItem]) -> str:
    lines = []
    for i, item in enumerate(items):
        traffic = f' (검색 {item.traffic:,}+)' if item.traffic else ''
        news = ''.join(f'\n    - {n.source}: {n.title}' for n in item.news[:3])
        lines.append(f'[{i}] {item.keyword}{traffic}{news}')
    return '\n'.join(lines)


def pick_trends(llm: LLMClient, model: str, items: Sequence[TrendItem], recent_titles: Sequence[str],
                min_score: int = MIN_TREND_SCORE) -> list[TrendPick]:
    """min_score 이상인 급상승 검색어를 점수·검색량 순으로. 검색어는 원래 급상승 검색어 그대로 고정한다."""
    if not items:
        return []
    recent = '\n'.join(f'- {t}' for t in recent_titles)
    user_input = f'최근 발행한 글 제목 (같은 소식이면 낮게):\n{recent}\n\n지금 급상승 검색어:\n{format_items(items)}'
    result = llm.parse(model, load_prompt('selector_trend'), user_input, TrendSelection, stage='select')
    picks: dict[int, TrendPick] = {}
    for c in result.choices:
        if 0 <= c.index < len(items) and c.score >= min_score and c.index not in picks:
            item = items[c.index]
            picks[c.index] = TrendPick(item, c.score, item.keyword, c.angle.strip())
    return sorted(picks.values(), key=lambda p: (p.score, p.item.traffic), reverse=True)


class RobotsCheck:
    """매체별 robots.txt를 한 번만 읽어 AI 수집 허용 여부를 기억한다."""

    def __init__(self, get: HttpGet) -> None:
        self._get = get
        self._cache: dict[str, RobotFileParser | None] = {}

    def _parser(self, origin: str) -> RobotFileParser | None:
        if origin not in self._cache:
            try:
                parser = RobotFileParser()
                parser.parse(self._get(f'{origin}/robots.txt').splitlines())
                self._cache[origin] = parser
            except requests.RequestException:
                self._cache[origin] = None
        return self._cache[origin]

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        parser = self._parser(f'{parts.scheme}://{parts.netloc}')
        return parser is not None and all(parser.can_fetch(agent, url) for agent in AI_AGENTS)


def _unwrap(link: str) -> str:
    return parse_qs(urlsplit(link).query).get('url', [link])[0] if 'apiclick' in link else link


def parse_bing_news(rss: str) -> tuple[NewsLink, ...]:
    links = []
    for item in ET.fromstring(rss).iter('item'):
        url = _unwrap(html.unescape(item.findtext('link') or '').strip())
        source = next((el.text or '' for el in item if el.tag.endswith('Source')), '')
        if url.startswith('http') and not any(h in urlsplit(url).netloc for h in SKIP_HOSTS):
            links.append(NewsLink(trends._clean(item.findtext('title') or ''), url, trends._clean(source)))
    return tuple(links)


def search_news(get: HttpGet, query: str) -> tuple[NewsLink, ...]:
    """검색어로 최근 기사 목록. 실패하면 빈 값 (구글 트렌드 기사·경제 RSS만으로 계속)."""
    try:
        return parse_bing_news(get(BING_NEWS_URL.format(q=quote_plus(query))))
    except (requests.RequestException, ET.ParseError) as e:
        print(f'[화제] 뉴스 검색 실패 ({e.__class__.__name__}): {query}')
        return ()


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r'[0-9A-Za-z가-힣]+', text) if len(w) >= 2}


def _matches(keyword: str, title: str) -> bool:
    """검색어가 제목에 그대로 있거나, 일반 낱말을 뺀 핵심 낱말이 모두(2개 넘으면 2개 이상) 제목에 들어 있다."""
    if trends.match_trending(title, (keyword,)):
        return True
    core = _tokens(keyword) - GENERIC_WORDS
    words = ''.join(re.findall(r'[0-9A-Za-z가-힣]+', title))
    hits = sum(1 for w in core if w in words)
    return bool(core) and hits >= min(len(core), MIN_SHARED_TOKENS)


def gather_sources(pick: TrendPick, pool: Sequence[Candidate], allows: Callable[[str], bool],
                   now: datetime, extra_news: Sequence[NewsLink] = (), limit: int = MAX_SOURCES) -> list[Candidate]:
    """구글 트렌드가 묶어 둔 기사 → 검색어로 찾은 기사(둘 다 AI 수집 허용 매체만) → 경제 언론사 RSS 중
    검색어 핵심 낱말이 제목에 든 기사 순으로 limit개."""
    found: list[Candidate] = []
    for news in (*pick.item.news, *extra_news):
        if allows(news.url):
            found.append(Candidate(news.title, news.url, '', now, news.source or urlsplit(news.url).netloc, True))
    found.extend(c for c in pool if _matches(pick.item.keyword, c.title))
    unique: list[Candidate] = []
    for c in found:
        if c.link not in {u.link for u in unique}:
            unique.append(c)
    return unique[:limit]
