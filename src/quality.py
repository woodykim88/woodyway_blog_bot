"""발행 전 자동 품질 검수. 문제 목록이 비어 있으면 통과."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.history import HistoryEntry
from src.llm import LLMClient, load_prompt
from src.models import Article, FactCheck, PostDraft
from src.selector import TITLE_SIMILARITY_THRESHOLD, title_similarity

MIN_BODY_CHARS = 1200
MAX_BODY_CHARS = 2500
MAX_TITLE_CHARS = 40
MAX_META_CHARS = 150
BANNED_WORDS = ('충격', '경악', '필수', '모르면 손해', '대박', '역대급', '무조건', '100%')


@dataclass(frozen=True)
class QualityReport:
    issues: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.issues


def body_text(draft: PostDraft) -> str:
    """분량 계산용 본문 평문."""
    parts = [
        draft.one_line_summary,
        *draft.key_points,
        *(f'{s.heading}\n{s.body}' for s in draft.sections),
        draft.why_it_matters,
        *draft.action_items,
    ]
    return '\n'.join(parts)


def fact_check_text(draft: PostDraft) -> str:
    """사실 검증 대상 본문. '내일 바로 써먹기'는 원래 원문에 없는 조언을 쓰는 자리라 제외합니다."""
    parts = [
        draft.title,
        draft.one_line_summary,
        *draft.key_points,
        *(f'{s.heading}\n{s.body}' for s in draft.sections),
        draft.why_it_matters,
    ]
    return '\n'.join(parts)


def check_structure(draft: PostDraft) -> list[str]:
    issues = []
    if not 3 <= len(draft.key_points) <= 5:
        issues.append(f'핵심 내용 불릿은 3~5개여야 합니다 (현재 {len(draft.key_points)}개).')
    if not 2 <= len(draft.sections) <= 3:
        issues.append(f'자세히 보기 섹션은 2~3개여야 합니다 (현재 {len(draft.sections)}개).')
    if not 1 <= len(draft.action_items) <= 3:
        issues.append(f'내일 바로 써먹기는 1~3개여야 합니다 (현재 {len(draft.action_items)}개).')
    if not 1 <= len(draft.labels) <= 2:
        issues.append(f'라벨은 1~2개여야 합니다 (현재 {len(draft.labels)}개).')
    if len(draft.title) > MAX_TITLE_CHARS:
        issues.append(f'제목이 {MAX_TITLE_CHARS}자를 넘습니다 (현재 {len(draft.title)}자).')
    if len(draft.meta_description) > MAX_META_CHARS:
        issues.append(f'메타 설명이 {MAX_META_CHARS}자를 넘습니다 (현재 {len(draft.meta_description)}자).')
    return issues


def check_length(draft: PostDraft) -> list[str]:
    length = len(body_text(draft))
    if not MIN_BODY_CHARS <= length <= MAX_BODY_CHARS:
        return [f'본문 분량은 {MIN_BODY_CHARS}~{MAX_BODY_CHARS}자여야 합니다 (현재 {length}자).']
    return []


def check_banned_words(draft: PostDraft) -> list[str]:
    text = f'{draft.title}\n{body_text(draft)}'
    found = [w for w in BANNED_WORDS if w in text]
    return [f'과장/낚시성 표현을 빼세요: {", ".join(found)}'] if found else []


def check_duplicate_title(draft: PostDraft, history: tuple[HistoryEntry, ...]) -> list[str]:
    for e in history[-30:]:
        if title_similarity(e.title, draft.title) >= TITLE_SIMILARITY_THRESHOLD:
            return [f'최근 글과 제목이 비슷합니다: "{e.title}"']
    return []


def check_source(article: Article, is_alive: Callable[[str], bool]) -> list[str]:
    return [] if is_alive(article.source_url) else [f'출처 링크가 열리지 않습니다: {article.source_url}']


def check_facts(llm: LLMClient, model: str, draft: PostDraft, article: Article) -> list[str]:
    user_input = f'--- 원문 ---\n{article.text}\n\n--- 작성된 글 ---\n{fact_check_text(draft)}'
    result = llm.parse(model, load_prompt('factcheck'), user_input, FactCheck)
    return [
        f'원문에 근거 없는 사실: "{c.sentence}" ({c.reason})'
        for c in result.claims
        if c.kind == 'unsupported_fact'
    ]


def run_quality_gate(
    llm: LLMClient,
    fast_model: str,
    draft: PostDraft,
    article: Article,
    history: tuple[HistoryEntry, ...],
    is_alive: Callable[[str], bool],
) -> QualityReport:
    """값싼 규칙 검사를 먼저 하고, 모두 통과했을 때만 LLM 사실 검증을 호출합니다."""
    issues = [
        *check_structure(draft),
        *check_length(draft),
        *check_banned_words(draft),
        *check_duplicate_title(draft, history),
        *check_source(article, is_alive),
    ]
    if not issues:
        issues.extend(check_facts(llm, fast_model, draft, article))
    return QualityReport(tuple(issues))
