"""20:00 칸 글감: 검색형 생활 가이드 ("근로장려금 신청 방법", "퇴직금 계산 방법" …).

뉴스 고쳐 쓰기 글은 큰 언론사와 겨뤄 검색 노출이 거의 없었다 (Search Console 28일 클릭 1·노출 7, 2026-10-03).
그래서 20:00 칸은 사람들이 실제로 검색창에 치는 말("○○ 하는 법/조회 방법/계산/신청 방법·자격·기간")을
정부·공공기관 공식 안내 페이지만 근거로 풀어 쓴다 (2026-10-03 사용자 결정).
- 글감 목록: data/howto_guides.json (공식 안내 1~3곳, 제철 달, 맞는 계산기)
- 고르는 순서: 제철(또는 사계절) 글감 중 안 쓴 것 → 제철 글감 중 가장 오래전에 쓴 것 → 제철 아닌 글감 (발행 건너뛰기 금지)
- 공식 페이지를 하나도 못 읽으면 그 글감은 건너뛴다. 첫 번째로 읽힌 페이지가 원문, 나머지는 함께 본 공식 안내.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from src.history import HistoryEntry
from src.models import Article, Candidate, Reference
from src.schedule import KST

GUIDES_PATH = Path(__file__).resolve().parent.parent / 'data' / 'howto_guides.json'
MAX_SOURCES = 3
MAX_EXTRA_SOURCE_CHARS = 4000  # 함께 본 공식 안내는 앞부분만 (원고 모델 입력 비용 억제)


@dataclass(frozen=True)
class Source:
    name: str  # 기관과 페이지 이름 (출처 목록에 그대로 나간다)
    url: str


@dataclass(frozen=True)
class HowtoGuide:
    query: str                    # 사람들이 검색창에 치는 말 그대로 (글의 검색어·제목 앞부분)
    area: str                     # 두 번째 라벨 (생활경제·재테크·지원금·혜택·절약꿀팁·부동산)
    slug: str                     # 글 주소용 영문 슬러그
    sources: tuple[Source, ...]   # 공식 안내 1~3곳
    months: tuple[int, ...] = ()  # 검색이 몰리는 달. 비어 있으면 사계절 글감
    tool: str = ''                # 맞는 계산기 id (tools.woodyway88.com/<id>/)

    @property
    def urls(self) -> tuple[str, ...]:
        return tuple(s.url for s in self.sources)

    def in_season(self, month: int) -> bool:
        return not self.months or month in self.months


def _parse(row: dict) -> HowtoGuide | None:
    sources = tuple(
        Source(name=str(s.get('name', '')).strip(), url=str(s.get('url', '')).strip())
        for s in row.get('sources', ())
        if str(s.get('url', '')).startswith('https://') and str(s.get('name', '')).strip()
    )[:MAX_SOURCES]
    query = str(row.get('query', '')).strip()
    if not query or not sources:
        return None
    months = tuple(int(m) for m in row.get('months', ()) if 1 <= int(m) <= 12)
    return HowtoGuide(query=query, area=str(row.get('area') or '생활경제'), slug=str(row.get('slug', '')).strip(),
                      sources=sources, months=months, tool=str(row.get('tool', '')).strip())


def load_guides(path: Path = GUIDES_PATH) -> tuple[HowtoGuide, ...]:
    """글감 목록. 파일이 없으면 빈 목록, 검색어나 https 공식 출처가 없는 줄은 건너뛴다."""
    if not path.exists():
        return ()
    rows = json.loads(path.read_text(encoding='utf-8'))
    return tuple(g for g in (_parse(r) for r in rows) if g is not None)


def _rank(group: Sequence[HowtoGuide], used_at: Callable[[HowtoGuide], int | None]) -> list[HowtoGuide]:
    """안 쓴 글감(목록 순서) 다음에 쓴 글감(가장 오래전에 쓴 순)."""
    fresh = [g for g in group if used_at(g) is None]
    used = sorted((g for g in group if used_at(g) is not None), key=lambda g: used_at(g) or 0)
    return [*fresh, *used]


def order_guides(guides: Sequence[HowtoGuide], past: Sequence[HistoryEntry], now: datetime) -> list[HowtoGuide]:
    """제철(또는 사계절) 글감을 먼저: 안 쓴 것 → 오래전에 쓴 것. 제철 아닌 글감은 맨 뒤 (같은 규칙)."""
    month = now.astimezone(KST).month
    last_used: dict[str, int] = {}
    for i, entry in enumerate(past):
        last_used[entry.source_url] = i

    def used_at(guide: HowtoGuide) -> int | None:
        hits = [last_used[u] for u in guide.urls if u in last_used]
        return max(hits) if hits else None

    seasonal = [g for g in guides if g.in_season(month)]
    off = [g for g in guides if not g.in_season(month)]
    return [*_rank(seasonal, used_at), *_rank(off, used_at)]


def to_candidate(guide: HowtoGuide, source: Source, now: datetime) -> Candidate:
    return Candidate(title=guide.query, link=source.url, summary=guide.query, published=now,
                     source=source.name, direct=True)


def build_article(
    guide: HowtoGuide, fetch: Callable[[Candidate], Article | None], now: datetime,
) -> Article | None:
    """공식 페이지들을 읽어 하나의 원문으로. 첫 번째로 읽힌 페이지가 원문, 나머지는 '함께 본 공식 안내'."""
    main: Article | None = None
    parts: list[str] = []
    refs: list[Reference] = []
    for source in guide.sources:
        article = fetch(to_candidate(guide, source, now))
        if article is None:
            continue
        if main is None:
            main = article
            parts.append(f'--- 공식 안내 1: {source.name} ---\n{article.text}')
            continue
        refs.append(Reference(source.name, source.name, article.source_url))
        parts.append(f'--- 공식 안내 {len(refs) + 1}: {source.name} ---\n{article.text[:MAX_EXTRA_SOURCE_CHARS]}')
    if main is None:
        return None
    return replace(main, text='\n\n'.join(parts), references=tuple(refs))


def iter_guides(
    past: Sequence[HistoryEntry],
    fetch: Callable[[Candidate], Article | None],
    now: datetime,
    guides: Sequence[HowtoGuide] | None = None,
) -> Iterator[tuple[HowtoGuide, Article]]:
    """공식 페이지를 읽어 낸 글감을 차례로. 필요한 만큼만 페이지를 연다."""
    for guide in order_guides(load_guides() if guides is None else guides, past, now):
        article = build_article(guide, fetch, now)
        if article is not None:
            yield guide, article
