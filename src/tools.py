"""정보마스터 계산기(tools.woodyway88.com) 목록. 글 주제와 맞는 계산기가 있으면 본문에 링크를 붙인다.

계산기 목록은 별도 저장소(~/Projects/정보마스터 계산기)의 pages/ 와 같아야 한다. 계산기를 새로 만들면 여기에도 추가.
- keywords: 제목·검색어·메타 설명·핵심 내용에 하나라도 있으면 그 계산기 (모든 뉴스 글)
- context_keywords: 생활경제 글에서만, 본문에 서로 다른 낱말이 MIN_CONTEXT_HITS개 이상 나오면 그 계산기
  (9/30 이후 링크가 0건이라 넓혔다. 2026-10-03)
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from src.models import PostDraft

TOOLS_PATH = Path(__file__).resolve().parent.parent / 'data' / 'tools.json'
MONEY = 'money'  # topics.MONEY (순환 import를 피하려고 값만 둔다)
NO_TOOL_CATEGORIES = frozenset({'skill', 'guide'})  # 12:30 칸 AI 글에는 계산기 링크를 달지 않는다
MIN_CONTEXT_HITS = 2


@dataclass(frozen=True)
class Tool:
    title: str
    url: str
    keywords: tuple[str, ...]
    blurb: str
    context_keywords: tuple[str, ...] = ()


def load_tools(path: Path = TOOLS_PATH) -> tuple[Tool, ...]:
    """계산기 목록을 읽습니다. 파일이 없으면 빈 목록, https가 아닌 주소는 건너뜁니다."""
    if not path.exists():
        return ()
    rows = json.loads(path.read_text(encoding='utf-8'))
    return tuple(
        Tool(title=r['title'], url=r['url'], keywords=tuple(r['keywords']), blurb=r.get('blurb', ''),
             context_keywords=tuple(r.get('context_keywords', ())))
        for r in rows
        if str(r.get('url', '')).startswith('https://') and r.get('keywords')
    )


def _squash(text: str) -> str:
    return text.replace(' ', '').lower()


def _headline_text(draft: PostDraft) -> str:
    return ' '.join([draft.title, draft.search_keyword, draft.meta_description, *draft.key_points])


def _full_text(draft: PostDraft) -> str:
    return ' '.join([_headline_text(draft), draft.hook, *(f'{s.heading} {s.body} {s.tip}' for s in draft.sections),
                     draft.why_it_matters, *draft.action_items])


def context_hits(tool: Tool, text: str) -> int:
    haystack = _squash(text)
    return sum(1 for k in tool.context_keywords if _squash(k) in haystack)


def match_tool(draft: PostDraft, tools: Sequence[Tool], category: str | None = None) -> Tool | None:
    """글 주제와 맞는 계산기. 핵심 낱말이 제목 근처에 있으면 바로, 생활경제 글은 본문 문맥으로도 찾는다."""
    if category in NO_TOOL_CATEGORIES:
        return None
    headline = _squash(_headline_text(draft))
    strong = next((t for t in tools if any(_squash(k) in headline for k in t.keywords)), None)
    if strong is not None or category != MONEY:
        return strong
    text = _full_text(draft)
    body = _squash(text)
    in_body = next((t for t in tools if any(_squash(k) in body for k in t.keywords)), None)
    if in_body is not None:
        return in_body
    scored = [(context_hits(t, text), i, t) for i, t in enumerate(tools)]
    best = max(scored, default=None, key=lambda x: (x[0], -x[1]))
    return best[2] if best and best[0] >= MIN_CONTEXT_HITS else None
