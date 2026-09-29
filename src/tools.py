"""정보마스터 계산기(tools.woodyway88.com) 목록. 글 주제와 맞는 계산기가 있으면 본문에 링크를 붙인다."""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from src.models import PostDraft

TOOLS_PATH = Path(__file__).resolve().parent.parent / 'data' / 'tools.json'


@dataclass(frozen=True)
class Tool:
    title: str
    url: str
    keywords: tuple[str, ...]
    blurb: str


def load_tools(path: Path = TOOLS_PATH) -> tuple[Tool, ...]:
    """계산기 목록을 읽습니다. 파일이 없으면 빈 목록, https가 아닌 주소는 건너뜁니다."""
    if not path.exists():
        return ()
    rows = json.loads(path.read_text(encoding='utf-8'))
    return tuple(
        Tool(title=r['title'], url=r['url'], keywords=tuple(r['keywords']), blurb=r.get('blurb', ''))
        for r in rows
        if str(r.get('url', '')).startswith('https://') and r.get('keywords')
    )


def _squash(text: str) -> str:
    return text.replace(' ', '').lower()


def match_tool(draft: PostDraft, tools: Sequence[Tool]) -> Tool | None:
    """제목이나 검색어에 계산기 키워드가 들어 있으면 그 계산기를 돌려줍니다."""
    haystack = _squash(f'{draft.title} {draft.search_keyword}')
    return next((t for t in tools if any(_squash(k) in haystack for k in t.keywords)), None)
