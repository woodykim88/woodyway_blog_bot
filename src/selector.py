"""주제 후보를 중복 제거하고 LLM으로 직장인 유용도를 채점해 순위를 매긴다."""
from __future__ import annotations

from collections.abc import Sequence
from difflib import SequenceMatcher

from src.history import HistoryEntry
from src.llm import LLMClient, load_prompt
from src.models import Candidate, RankedCandidate, SelectionResult

TITLE_SIMILARITY_THRESHOLD = 0.6
MIN_SCORE = 6
FALLBACK_MIN_SCORE = 4  # 두 분야 모두 MIN_SCORE 이상이 없을 때만 쓴다 (발행을 건너뛰지 않으려는 안전장치)
RECENT_HISTORY_SIZE = 30

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


def rank_candidates(
    llm: LLMClient, model: str, candidates: list[Candidate], theme: str, prompt: str = 'selector',
    recent_titles: Sequence[str] = (), trending: Sequence[str] = (), min_score: int = MIN_SCORE,
) -> list[RankedCandidate]:
    """min_score 이상인 후보를 점수 높은 순으로 반환합니다. 같은 소식은 점수가 가장 높은 하나만 남깁니다."""
    if not candidates:
        return []
    user_input = f'오늘의 테마: {theme}\n\n{format_recent(recent_titles)}{format_trending(trending)}후보 목록:\n{format_candidates(candidates)}'
    result = llm.parse(model, load_prompt(prompt), user_input, SelectionResult)

    best: dict[int, tuple[RankedCandidate, str, bool]] = {}
    for s in result.scores:
        if 0 <= s.index < len(candidates) and s.score >= min_score and s.index not in best:
            best[s.index] = (RankedCandidate(candidates[s.index], s.score, s.one_line.strip()), story_key(s.story), s.already_covered)

    seen: set[str] = set()
    unique = []
    for ranked, key, covered in sorted(best.values(), key=lambda item: item[0].score, reverse=True):
        if key and key in seen:
            continue
        seen.add(key)  # 이미 쓴 소식이면 같은 소식의 다른 출처 후보도 함께 막힌다
        if not covered:
            unique.append(ranked)
    return unique
