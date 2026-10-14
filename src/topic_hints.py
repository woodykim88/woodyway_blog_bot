"""지식창고 키워드 조사에서 이 채널에 배분된 오늘의 소재 (2026-10-15 사용자 지시).

조사는 로컬에서 하고(카카오 키워드 플래너 로그인 필요), ~/Projects/지식창고/scripts/sync_hints.py가
data/topic_hints.json으로 각 저장소에 넣어 푸시한다. 파일이 없거나 오래됐으면 빈 목록 — 평소대로 동작한다.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

CHANNEL = 'blog'
HINTS_PATH = Path(__file__).resolve().parent.parent / 'data' / 'topic_hints.json'
MAX_AGE_DAYS = 3  # 조사한 날부터 이 일수까지만 쓴다 (시의성이 생명이라)
MIN_KEYWORD_CHARS = 2


def load(today: date, path: Path = HINTS_PATH, channel: str = CHANNEL) -> list[dict[str, Any]]:
    """오늘 쓸 수 있는 이 채널의 소재 목록. [{topic, keywords, angle}, ...]"""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        age = (today - date.fromisoformat(data['date'])).days
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if not 0 <= age <= MAX_AGE_DAYS:
        return []
    items = data.get('channels', {}).get(channel, [])
    return [i for i in items if isinstance(i, dict) and i.get('topic')]


def _squash(text: str) -> str:
    return re.sub(r'\s+', '', text).lower()


def matches(text: str, hints: list[dict[str, Any]]) -> list[str]:
    """글(후보 제목·요약)에 조사 키워드가 그대로 들어 있는 소재 이름들 (띄어쓰기 무시)."""
    squashed = _squash(text)
    return [h['topic'] for h in hints
            if any(len(_squash(k)) >= MIN_KEYWORD_CHARS and _squash(k) in squashed for k in h.get('keywords', []))]


def brief(hints: list[dict[str, Any]], usage: str) -> str:
    """프롬프트 끝에 붙이는 안내. usage는 채널별 쓰임 설명."""
    if not hints:
        return ''
    lines = '\n'.join(f"- {h['topic']} (검색어: {', '.join(h.get('keywords', [])[:4])})"
                      + (f" — {h['angle']}" if h.get('angle') else '') for h in hints)
    return f'\n\n[오늘 사람들이 많이 찾는 소재 — 키워드 데이터 조사 결과]\n{usage}\n{lines}\n'
