"""20:00 칸 글감: 검색형 생활 가이드 ("근로장려금 신청 방법", "퇴직금 계산 방법" …).

뉴스 고쳐 쓰기 글은 큰 언론사와 겨뤄 검색 노출이 거의 없었다 (Search Console 28일 클릭 1·노출 7, 2026-10-03).
그래서 20:00 칸은 사람들이 실제로 검색창에 치는 말("○○ 하는 법/조회 방법/계산/신청 방법·자격·기간")을
정부·공공기관 공식 안내 페이지만 근거로 풀어 쓴다 (2026-10-03 사용자 결정).
- 글감 목록: data/howto_guides.json (공식 안내 1~3곳, 제철 달, 맞는 계산기)
- 2026-10-10: "실업급여 신청 방법" 같은 큰 검색어는 정부·대형 사이트에 밀려 28일 노출이 0이었다. 목록 앞쪽에
  구글 자동완성에 실제로 뜨는 롱테일 검색어("퇴직금 1년 미만", "묵시적 갱신 해지 3개월")를 두고 먼저 쓴다.
  새 글감은 자동완성에 뜨는지, 공식 안내 본문에 답이 있는지 확인하고 넣는다.
- 고르는 순서: 제철(또는 사계절) 글감 중 안 쓴 것 → 제철 글감 중 가장 오래전에 쓴 것 → 제철 아닌 글감 (발행 건너뛰기 금지)
- 공식 페이지를 하나도 못 읽으면 그 글감은 건너뛴다. 첫 번째로 읽힌 페이지가 원문, 나머지는 함께 본 공식 안내.
- 2026-10-18 방향 전환(애드센스 실측: 실제 방문 하루 ~2회, 검색 클릭은 계산기 사이트 캘린더뿐): 글감에 종류(kind)를 둔다.
  계산 사례(case: 실제 숫자로 끝까지 계산) · 일정(schedule: 마감·신청·연휴) · 숫자 변경(change: 2027 최저임금처럼
  내 돈 계산이 바뀌는 확정 소식) · 기존 안내(guide). 요일마다 정해 둔 종류의 안 쓴 글감을 먼저 쓴다 (KIND_BY_WEEKDAY).
- 계산 예시(examples): 원고 모델이 직접 계산하면 검수가 "원문에 없는 금액"으로 걸러 숫자 없는 글이 나왔다 (2026-10-18 미리보기).
  그래서 예시 금액은 계산기와 같은 공식(정보마스터-계산기 lib)으로 미리 계산해 글감에 넣고, 원문 뒤에 붙여
  원고·검수 모두 근거로 쓴다.
- 2026-10-21 지식창고 조사 연결: 키워드 데이터로 고른 그날의 소재(data/topic_hints.json, 3일 유효)를 쓴다.
  ① 조사가 공식 출처까지 확인해 보낸 글감(hints 항목의 howto)은 오늘의 종류와 같으면 맨 먼저,
  ② 목록 글감 중 조사 키워드가 검색어에 들어간 것은 같은 종류 안에서 먼저. 요일별 종류 순환은 그대로 지킨다
  (조사는 재료일 뿐, 채널 구성이 우선 — 2026-10-15 사용자 원칙).
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from src import topic_hints
from src.history import HistoryEntry
from src.models import Article, Candidate, Reference
from src.schedule import KST

GUIDES_PATH = Path(__file__).resolve().parent.parent / 'data' / 'howto_guides.json'
MAX_SOURCES = 3
MAX_EXTRA_SOURCE_CHARS = 4000  # 함께 본 공식 안내는 앞부분만 (원고 모델 입력 비용 억제)

CASE = 'case'          # 계산 사례: "월급 300만 원 3년 근무 퇴직금"처럼 숫자를 넣어 끝까지 계산
SCHEDULE = 'schedule'  # 일정: 신청·마감·연휴 날짜
CHANGE = 'change'      # 숫자 변경: 확정된 금액·요율·기준 변경과 내 돈에 미치는 영향
GUIDE = 'guide'        # 기존 안내형 ("○○ 신청 방법")
KINDS = (CASE, SCHEDULE, CHANGE, GUIDE)
# 블로그 상단 메뉴가 이 라벨 목록 페이지로 연결된다 (2026-10-18). 기존 안내형은 종류 라벨이 없다
KIND_LABELS = {CASE: '계산사례', SCHEDULE: '일정', CHANGE: '바뀌는제도'}
# 월~일. 계산 사례 3 · 일정 2 · 숫자 변경 2 (2026-10-18 사용자 승인)
KIND_BY_WEEKDAY = (CASE, SCHEDULE, CASE, CHANGE, CASE, SCHEDULE, CHANGE)


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
    kind: str = GUIDE             # 글 종류 (KINDS). 종류별 글쓰기 규칙은 prompts/writer_howto_<kind>.md
    examples: tuple[str, ...] = ()  # 계산기 공식으로 미리 계산한 예시 문장 (원문 뒤에 붙는다)

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
    kind = str(row.get('kind') or GUIDE).strip()
    return HowtoGuide(query=query, area=str(row.get('area') or '생활경제'), slug=str(row.get('slug', '')).strip(),
                      sources=sources, months=months, tool=str(row.get('tool', '')).strip(),
                      kind=kind if kind in KINDS else GUIDE,
                      examples=tuple(str(x).strip() for x in row.get('examples', ()) if str(x).strip()))


def load_guides(path: Path = GUIDES_PATH) -> tuple[HowtoGuide, ...]:
    """글감 목록. 파일이 없으면 빈 목록, 검색어나 https 공식 출처가 없는 줄은 건너뛴다."""
    if not path.exists():
        return ()
    rows = json.loads(path.read_text(encoding='utf-8'))
    return tuple(g for g in (_parse(r) for r in rows) if g is not None)


def kind_of_day(now: datetime) -> str:
    return KIND_BY_WEEKDAY[now.astimezone(KST).weekday()]


def _kind_priority(want: str) -> Callable[[HowtoGuide], int]:
    """오늘의 종류 → 다른 새 종류(계산 사례·일정·숫자 변경) → 기존 안내형."""
    return lambda g: 0 if g.kind == want else (2 if g.kind == GUIDE else 1)


def hint_guides(hints: Sequence[dict]) -> tuple[HowtoGuide, ...]:
    """조사 소재 중 공식 출처까지 확인된 글감(howto). 검색어·https 출처가 없으면 버린다."""
    return tuple(g for g in (_parse(h['howto']) for h in hints if isinstance(h.get('howto'), dict)) if g is not None)


def _rank(
    group: Sequence[HowtoGuide], used_at: Callable[[HowtoGuide], int | None], want: str = '',
    boosted: Callable[[HowtoGuide], int] = lambda g: 0,
) -> list[HowtoGuide]:
    """안 쓴 글감(오늘의 종류 먼저, 같은 종류 안에서는 조사 소재 먼저, 같으면 목록 순서) 다음에 쓴 글감(가장 오래전에 쓴 순)."""
    fresh = [g for g in group if used_at(g) is None]
    if want:
        priority = _kind_priority(want)
        fresh = sorted(fresh, key=lambda g: (priority(g), boosted(g)))  # 안정 정렬이라 같은 칸 안에서는 목록 순서 유지
    used = sorted((g for g in group if used_at(g) is not None), key=lambda g: used_at(g) or 0)
    return [*fresh, *used]


MIN_TRUNCATED_SLUG = 25  # 이보다 짧은 앞부분은 우연히 겹칠 수 있어 잘린 주소로 보지 않는다


def _is_truncation(posted: str, slug: str) -> bool:
    """발행된 글 주소 슬러그가 글감 슬러그를 단어 경계에서 자른 앞부분인가."""
    return len(posted) >= MIN_TRUNCATED_SLUG and slug.startswith(posted + '-')


def order_guides(
    guides: Sequence[HowtoGuide], past: Sequence[HistoryEntry], now: datetime, hints: Sequence[dict] = (),
) -> list[HowtoGuide]:
    """제철(또는 사계절) 글감을 먼저: 안 쓴 것(오늘의 종류 먼저) → 오래전에 쓴 것. 제철 아닌 글감은 맨 뒤 (같은 규칙).
    hints(지식창고 조사)가 있으면 조사가 보낸 글감 → 조사 키워드가 든 목록 글감 순으로 같은 종류 안에서 앞세운다."""
    month = now.astimezone(KST).month
    want = kind_of_day(now)
    listed = {g.slug for g in guides if g.slug}
    research = [g for g in hint_guides(hints) if not g.slug or g.slug not in listed]
    guides = [*research, *guides]

    def boosted(g: HowtoGuide) -> int:
        if g in research:
            return 0
        return 1 if topic_hints.matches(g.query, list(hints)) else 2

    last_used: dict[str, int] = {}
    last_slug: dict[str, int] = {}
    for i, entry in enumerate(past):
        last_used[entry.source_url] = i
        last_slug[entry.url.rsplit('/', 1)[-1].removesuffix('.html')] = i
    known_slugs = {g.slug for g in guides}
    shared = {u for u, n in Counter(u for g in guides for u in g.urls).items() if n > 1}

    def used_at(guide: HowtoGuide) -> int | None:
        """글 주소의 슬러그로 먼저 찾는다. 공식 안내 주소는 그 주소를 이 글감만 쓸 때에만 본다
        (2026-10-10: 롱테일 글감은 같은 공식 페이지를 여러 검색어가 나눠 쓴다)."""
        if guide.slug and guide.slug in last_slug:
            return last_slug[guide.slug]
        truncated = [i for posted, i in last_slug.items() if posted not in known_slugs and _is_truncation(posted, guide.slug)]
        if truncated:
            return max(truncated)  # Blogger가 긴 주소를 잘라 슬러그가 앞부분만 남은 글 (2026-10-11)
        hits = [last_used[u] for u in guide.urls if u in last_used and u not in shared]
        return max(hits) if hits else None

    seasonal = [g for g in guides if g.in_season(month)]
    off = [g for g in guides if not g.in_season(month)]
    return [*_rank(seasonal, used_at, want, boosted), *_rank(off, used_at, want, boosted)]


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
    if guide.examples:
        parts.append('--- 계산 예시 (공식 계산식을 그대로 적용해 계산한 값) ---\n' + '\n'.join(f'- {x}' for x in guide.examples))
    return replace(main, text='\n\n'.join(parts), references=tuple(refs))


def iter_guides(
    past: Sequence[HistoryEntry],
    fetch: Callable[[Candidate], Article | None],
    now: datetime,
    guides: Sequence[HowtoGuide] | None = None,
) -> Iterator[tuple[HowtoGuide, Article]]:
    """공식 페이지를 읽어 낸 글감을 차례로. 필요한 만큼만 페이지를 연다."""
    hints = topic_hints.load(now.astimezone(KST).date()) if guides is None else ()
    for guide in order_guides(load_guides() if guides is None else guides, past, now, hints):
        article = build_article(guide, fetch, now)
        if article is not None:
            yield guide, article
