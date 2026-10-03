"""12:30 칸 예비 글감: 대중적인 AI 도구의 공식 도움말 한 페이지로 '사용법' 글을 쓴다.

스킬 글을 고르지 못한 날에도 12:30 칸은 AI 도구 사용법 글이어야 한다 (2026-10-03 사용자 결정).
그 전에는 일반 IT 뉴스로 대신해 10/02·10/03 칸이 스킬 글이 아니었다.
- 글감 목록: data/ai_guides.json (공식 문서라 사실검증 근거가 분명하다)
- 아직 안 쓴 페이지부터. 다 썼으면 가장 오래전에 쓴 페이지를 다시 쓴다 (발행 건너뛰기 금지)
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.history import HistoryEntry
from src.models import Article, Candidate

GUIDES_PATH = Path(__file__).resolve().parent.parent / 'data' / 'ai_guides.json'
SOURCE_NAME = '공식 도움말'


@dataclass(frozen=True)
class Guide:
    tool: str     # 도구 이름 (Claude Code, Gemini, NotebookLM …)
    keyword: str  # 한국 사람이 검색할 말 (글의 검색어 후보)
    url: str      # 공식 문서 주소 (원문)


def load_guides(path: Path = GUIDES_PATH) -> tuple[Guide, ...]:
    """글감 목록. 파일이 없거나 https가 아닌 주소는 건너뛴다."""
    if not path.exists():
        return ()
    rows = json.loads(path.read_text(encoding='utf-8'))
    return tuple(
        Guide(tool=r['tool'], keyword=r['keyword'], url=r['url'])
        for r in rows
        if str(r.get('url', '')).startswith('https://') and r.get('tool') and r.get('keyword')
    )


def order_guides(guides: Sequence[Guide], past: Sequence[HistoryEntry]) -> list[Guide]:
    """안 쓴 글감을 목록 순서대로 먼저, 그다음 가장 오래전에 쓴 글감부터."""
    last_used: dict[str, int] = {}
    for i, entry in enumerate(past):
        last_used[entry.source_url] = i
    fresh = [g for g in guides if g.url not in last_used]
    used = sorted((g for g in guides if g.url in last_used), key=lambda g: last_used[g.url])
    return [*fresh, *used]


def to_candidate(guide: Guide, now: datetime) -> Candidate:
    return Candidate(
        title=f'{guide.tool} 사용법: {guide.keyword}', link=guide.url, summary=guide.keyword,
        published=now, source=f'{guide.tool} {SOURCE_NAME}', direct=True,
    )


def iter_guides(
    past: Sequence[HistoryEntry],
    fetch: Callable[[Candidate], Article | None],
    now: datetime,
    guides: Sequence[Guide] | None = None,
) -> Iterator[tuple[Guide, Article]]:
    """본문을 읽어 낸 글감을 차례로. 필요한 만큼만 페이지를 연다. 안 열리는 페이지는 건너뛴다."""
    for guide in order_guides(load_guides() if guides is None else guides, past):
        article = fetch(to_candidate(guide, now))
        if article is not None:
            yield guide, article
