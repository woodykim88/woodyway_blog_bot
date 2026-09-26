"""주제 후보를 중복 제거하고 LLM으로 직장인 유용도를 채점해 순위를 매긴다."""
from __future__ import annotations

from difflib import SequenceMatcher

from src.history import HistoryEntry
from src.llm import LLMClient, load_prompt
from src.models import Candidate, RankedCandidate, SelectionResult

TITLE_SIMILARITY_THRESHOLD = 0.6
MIN_SCORE = 6
RECENT_HISTORY_SIZE = 30

# 월(0) ~ 일(6)
WEEKDAY_THEMES = ('AI활용', '툴추천', 'IT트렌드', '업무생산성', '커리어', '자유 주제', '자유 주제')


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


def rank_candidates(
    llm: LLMClient, model: str, candidates: list[Candidate], theme: str
) -> list[RankedCandidate]:
    """MIN_SCORE 이상인 후보를 점수 높은 순으로 반환합니다. 같은 소식은 점수가 가장 높은 하나만 남깁니다."""
    if not candidates:
        return []
    user_input = f'오늘의 테마: {theme}\n\n후보 목록:\n{format_candidates(candidates)}'
    result = llm.parse(model, load_prompt('selector'), user_input, SelectionResult)

    best: dict[int, tuple[RankedCandidate, str]] = {}
    for s in result.scores:
        if 0 <= s.index < len(candidates) and s.score >= MIN_SCORE and s.index not in best:
            best[s.index] = (RankedCandidate(candidates[s.index], s.score, s.one_line.strip()), story_key(s.story))

    seen: set[str] = set()
    unique = []
    for ranked, key in sorted(best.values(), key=lambda pair: pair[0].score, reverse=True):
        if key and key in seen:
            continue
        seen.add(key)
        unique.append(ranked)
    return unique
