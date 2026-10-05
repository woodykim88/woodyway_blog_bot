"""주제 후보를 중복 제거하고 LLM으로 직장인 유용도를 채점해 순위를 매긴다."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from difflib import SequenceMatcher

from src import kr_calendar
from src.history import HistoryEntry
from src.llm import LLMClient, load_prompt
from src.models import Candidate, RankedCandidate, SelectionResult

TITLE_SIMILARITY_THRESHOLD = 0.6
MIN_SCORE = 6
FALLBACK_MIN_SCORE = 4  # 두 분야 모두 MIN_SCORE 이상이 없을 때만 쓴다 (발행을 건너뛰지 않으려는 안전장치)
RECENT_HISTORY_SIZE = 30
MIN_SHARED_WORDS = 2  # 제목 낱말이 이만큼 겹치면 같은 소식으로 본다 (모델이 소식 이름을 안 줬을 때만)
_STOP_WORDS = frozenset({'있다', '없다', '한다', '이번', '올해', '내년', '오늘', '지난', '위해', '관련', '정부', '대한'})
MAX_RELATED = 2  # 같은 소식을 다룬 다른 기사를 이만큼까지 함께 읽혀 비교·보충하게 한다 (기사 1건 재작성 방지)

def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.replace(' ', ''), b.replace(' ', '')).ratio()


def is_duplicate(candidate: Candidate, history: tuple[HistoryEntry, ...]) -> bool:
    recent = history[-RECENT_HISTORY_SIZE:]
    return any(
        e.geeknews_url == candidate.link
        or title_similarity(e.title, candidate.title) >= TITLE_SIMILARITY_THRESHOLD
        for e in recent
    )


def story_key(story: str) -> str:
    """같은 소식 판별용 키. 모델이 "마이크로소프트의 코파일럿"처럼 조사를 붙여도 같게 봅니다."""
    return story.replace(' ', '').replace('의', '').lower()


def format_candidates(candidates: list[Candidate]) -> str:
    return '\n'.join(f'[{i}] ({c.source}) {c.title}\n    {c.summary[:300]}' for i, c in enumerate(candidates))


def format_recent(recent_titles: Sequence[str]) -> str:
    """이미 쓴 소식을 모델이 알아보게 한다. 후보 제목과 글 제목은 표현이 달라 코드로는 같은 소식을 못 잡는다."""
    if not recent_titles:
        return ''
    return '최근 발행한 글 제목 (같은 소식이면 낮게 채점):\n' + '\n'.join(f'- {t}' for t in recent_titles) + '\n\n'


def format_trending(trending: Sequence[str]) -> str:
    """지금 많이 검색되는 키워드. 후보가 직접 관련되면 가점을 주라고 프롬프트가 안내한다."""
    if not trending:
        return ''
    return '지금 한국에서 많이 검색되는 키워드:\n' + '\n'.join(f'- {k}' for k in trending) + '\n\n'


def format_calendar(day: date | None) -> str:
    """발행일의 달력 사실. 휴일 직전·연휴 중이면 그 맥락에 맞는 후보에 가점을 주라고 안내한다."""
    if day is None:
        return ''
    text = f'발행일 달력 (한국 공휴일·대체공휴일 반영):\n{kr_calendar.calendar_facts(day)}\n'
    if kr_calendar.holiday_context(day):
        text += '휴일·연휴 직전이거나 연휴 중이면, 연휴 준비·휴일 운영·이동·소비 같은 휴일 맥락과 맞는 후보를 조금 높게 채점하세요 (맞는 후보가 없으면 평소대로).\n'
    return text + '\n'


def rank_candidates(
    llm: LLMClient, model: str, candidates: list[Candidate], theme: str, prompt: str = 'selector',
    recent_titles: Sequence[str] = (), trending: Sequence[str] = (), min_score: int = MIN_SCORE, day: date | None = None,
) -> list[RankedCandidate]:
    """min_score 이상인 후보를 점수 높은 순으로 반환합니다. 같은 소식은 점수가 가장 높은 하나만 남깁니다."""
    if not candidates:
        return []
    user_input = f'오늘의 테마: {theme}\n\n{format_calendar(day)}{format_recent(recent_titles)}{format_trending(trending)}후보 목록:\n{format_candidates(candidates)}'
    result = llm.parse(model, load_prompt(prompt), user_input, SelectionResult, stage='select')

    best: dict[int, tuple[RankedCandidate, str, bool]] = {}
    by_story: dict[str, list[Candidate]] = {}  # 점수와 상관없이 같은 소식을 다룬 후보들
    for s in result.scores:
        if not 0 <= s.index < len(candidates):
            continue
        key = story_key(s.story)
        if key and candidates[s.index] not in by_story.setdefault(key, []):
            by_story[key].append(candidates[s.index])
        if s.score >= min_score and s.index not in best:
            best[s.index] = (RankedCandidate(candidates[s.index], s.score, s.one_line.strip()), key, s.already_covered)

    seen: set[str] = set()
    unique = []
    for ranked, key, covered in sorted(best.values(), key=lambda item: item[0].score, reverse=True):
        if key and key in seen:
            continue
        seen.add(key)  # 이미 쓴 소식이면 같은 소식의 다른 출처 후보도 함께 막힌다
        if not covered:
            same = ([c for c in by_story.get(key, []) if c.link != ranked.candidate.link]
                    or similar_titles(ranked.candidate, candidates))
            unique.append(replace(ranked, related=related_candidates(ranked.candidate, same)))
    return unique


def _title_words(title: str) -> set[str]:
    words = re.findall(r'[0-9A-Za-z가-힣]+', title)
    return {w.lower() for w in words if len(w) >= 2 and w not in _STOP_WORDS}


def similar_titles(main: Candidate, pool: Sequence[Candidate], min_shared: int = MIN_SHARED_WORDS) -> list[Candidate]:
    """모델이 소식 이름을 주지 않았을 때: 제목 낱말이 min_shared개 이상 겹치는 다른 기사."""
    words = _title_words(main.title)
    return [c for c in pool if c.link != main.link and len(words & _title_words(c.title)) >= min_shared]


def related_candidates(main: Candidate, same_story: Sequence[Candidate], limit: int = MAX_RELATED) -> tuple[Candidate, ...]:
    """같은 소식의 다른 기사. 다른 언론사 기사를 먼저 고른다 (같은 매체 기사는 시각이 겹친다)."""
    others = [c for c in same_story if c.link != main.link]
    others.sort(key=lambda c: c.source == main.source)
    return tuple(others[:limit])
