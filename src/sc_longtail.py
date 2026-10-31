"""Search Console 검색어로 롱테일 글감과 리라이트 대상을 고른다 (2026-10-31 사용자 지시).

네이버 블로그1에 같은 날 적용한 원리(경쟁 낮은 구체 문구를 제목 맨 앞에, 첫 3줄에 답·숫자)를 구글에 맞게 옮겼다.
- 실측(90일): 노출 269·클릭 6. "퇴직금 계산 방법" 같은 넓은 검색어 글은 50~76위, 클릭은 계산기 일정 페이지뿐.
  그래서 넓은 검색어("○○ 신청 방법")는 같은 종류 안에서 뒤로 미루고, 실제로 노출된 검색어(수요가 확인됐는데
  아직 첫 페이지가 아닌 것)가 들어간 글감을 먼저 쓴다.
- 이미 노출되는 글은 리라이트 후보로 뽑는다(계산기 연결 글 먼저). 리라이트는 사람이 확인하고 한다.
- 발행 기록(history.json)에 있는 글만 본다. 10/22 초안으로 내린 AI·IT 글(llm-api 등)도 검색 기록에는 한동안 남기 때문.
- 원자료는 search_console.py가 매일 data/search_console.json의 query_pages(90일 검색어×페이지)에 쓴다.
  검색어가 드러난 노출이 아직 적어(90일 77회) 효과는 데이터가 쌓이면서 커진다. 이 모듈은 구글 라이브러리 없이 돈다.
"""
from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from src.history import HistoryEntry

SC_PATH = Path(__file__).resolve().parent.parent / 'data' / 'search_console.json'
BLOG_HOST = 'www.woodyway88.com'
FIRST_PAGE = 10.0          # 평균 순위가 이보다 뒤면 "수요는 있는데 아직 못 잡은" 검색어
MIN_QUERY_CHARS = 4        # 띄어쓰기 뺀 글자 수. 이보다 짧은 검색어는 아무 글감에나 걸려 쓰지 않는다
REWRITE_MAX_POSITION = 60.0
REWRITE_LIMIT = 10
GENERIC_TAIL = ('방법', '조건', '자격', '기준', '법')
ACTION_WORDS = ('신청', '이용', '조회', '신고', '계산', '갱신', '가입', '수급', '받는', '하는')


def load(path: Path = SC_PATH) -> list[dict[str, Any]]:
    """query_pages 줄 목록. 파일이 없거나 형식이 다르면 빈 목록 (평소대로 동작)."""
    try:
        rows = json.loads(path.read_text(encoding='utf-8')).get('query_pages', [])
    except (OSError, ValueError, AttributeError):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get('query') and r.get('page')]


def _squash(text: str) -> str:
    return re.sub(r'\s+', '', text).lower()


def _is_blog(page: str) -> bool:
    return f'//{BLOG_HOST}/' in page


def demand_queries(rows: Sequence[Mapping[str, Any]], known: Collection[str] | None = None) -> list[str]:
    """블로그 글이 노출됐지만 첫 페이지가 아닌 검색어 (노출 많은 순, 중복 제거). known이 있으면 그 글 주소만."""
    picked = sorted((r for r in rows if _is_blog(r['page']) and r.get('position', 0) > FIRST_PAGE
                     and (known is None or r['page'] in known)),
                    key=lambda r: -r.get('impressions', 0))
    return list(dict.fromkeys(r['query'] for r in picked))


def matches_demand(query: str, demand: Sequence[str]) -> bool:
    """글감 검색어에 수요 검색어가 그대로 들었거나, 수요 검색어의 단어가 모두 들었는가 (어순 무시)."""
    target, words = _squash(query), set(query.lower().split())
    for d in demand:
        if len(_squash(d)) < MIN_QUERY_CHARS:
            continue
        if _squash(d) in target or set(d.lower().split()) <= words:
            return True
    return False


def is_generic(query: str) -> bool:
    """넓은 검색어인가: 숫자가 없고, 끝의 "신청 방법·수급 자격" 같은 말을 떼면 주제어 하나만 남는 검색어.
    예) 근로장려금 신청 방법, 전입신고 방법, 현금영수증 소득공제 → 넓음 / 퇴직금 1년 미만, 실업급여 자진퇴사 조건 → 구체적."""
    if re.search(r'\d', query):
        return False
    words = query.split()
    if words and words[-1] in GENERIC_TAIL:
        words = words[:-1]
        if words and words[-1] in ACTION_WORDS:
            words = words[:-1]
    return len(words) <= 1 or len(query.split()) <= 2


def rewrite_targets(rows: Sequence[Mapping[str, Any]], past: Sequence[HistoryEntry]) -> list[dict[str, Any]]:
    """노출은 있는데 클릭이 없거나 첫 페이지 밖인 블로그 글. 계산기 연결 글 먼저, 그다음 노출 많은 순."""
    tool_of = {e.url: e.tool for e in past if e.url}
    pages: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not _is_blog(r['page']) or r['page'] not in tool_of:
            continue
        p = pages.get(r['page'], {'page': r['page'], 'impressions': 0, 'clicks': 0, 'queries': []})
        pages[r['page']] = {**p, 'impressions': p['impressions'] + r['impressions'], 'clicks': p['clicks'] + r['clicks'],
                            'queries': [*p['queries'], r]}
    targets = []
    for p in pages.values():
        top = max(p['queries'], key=lambda q: q['impressions'])
        if p['clicks'] > 0 and top['position'] <= FIRST_PAGE:
            continue  # 이미 첫 페이지에서 클릭을 받는 글
        if top['position'] > REWRITE_MAX_POSITION and p['impressions'] < 3:
            continue
        targets.append({'page': p['page'], 'impressions': p['impressions'], 'clicks': p['clicks'],
                        'top_query': top['query'], 'position': top['position'], 'tool': tool_of.get(p['page'], '')})
    targets.sort(key=lambda t: (not t['tool'], -t['impressions']))
    return targets[:REWRITE_LIMIT]


def summary_lines(rows: Sequence[Mapping[str, Any]], past: Sequence[HistoryEntry]) -> list[str]:
    lines = ['### 리라이트 후보 (90일, 검색어×글 — 계산기 연결 글 먼저)']
    lines += [f"- {t['page'].rsplit('/', 1)[-1]}: 노출 {t['impressions']} · 클릭 {t['clicks']} · "
              f"'{t['top_query']}' 평균 {t['position']}위" + (f" · 계산기 {t['tool']}" if t['tool'] else '')
              for t in rewrite_targets(rows, past)] or ['- (없음)']
    demand = demand_queries(rows, {e.url for e in past if e.url})[:10]
    lines += ['', '### 수요는 있는데 아직 첫 페이지 밖인 검색어 (새 글감 우선 후보)']
    lines += [f'- {q}' for q in demand] or ['- (없음)']
    return lines
