"""구제 발행: 검수에 걸린 문장(원문을 옮겨 쓴 문장, 근거 없는 문장)만 빼고 발행한다.

원고 때문에 발행을 건너뛰지 않는다는 운영자 지시(2026-09-27)에 따른 것이다. 검수를 끄지는 않는다.
제목·핵심 도입부·문단 전체처럼 빼면 글이 성립하지 않는 곳이 걸리면 포기하고 다른 주제로 넘어간다.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import date

from src import quality
from src.history import HistoryEntry
from src.llm import LLMClient
from src.models import Article, PostDraft

MAX_PASSES = 2  # 빼고 나서 다시 검수했을 때 새로 걸린 문장을 한 번 더 뺀다
MIN_OVERLAP_CHARS = 8  # 복사 구간이 문장 끝·머리에 이만큼 이상 걸쳐야 그 문장을 뺀다 ("다."만 겹치는 이웃 문장은 살린다)
MIN_MATCH_CHARS = 6  # 근거 없는 문장을 본문에서 찾을 때 겹쳐야 하는 최소 글자 수 (공백 제외)
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?。])\s+')
_PARAGRAPH_SPLIT = re.compile(r'\n\s*\n|\n')


def _squash(text: str) -> str:
    return re.sub(r'\s+', '', text)


def _same_sentence(claim: str, sentence: str) -> bool:
    a, b = _squash(claim), _squash(sentence)
    return len(min(a, b, key=len)) >= MIN_MATCH_CHARS and (a in b or b in a)


def _clean_text(text: str, spans: list[tuple[int, int]], claims: Sequence[str]) -> tuple[str, int]:
    """문단은 유지하고, 복사 구간과 겹치거나 걸린 문장만 뺍니다. (새 글, 뺀 문장 수)"""
    paragraphs, offset, removed = [], 0, 0
    for paragraph in _PARAGRAPH_SPLIT.split(text):
        kept = []
        for sentence in _SENTENCE_SPLIT.split(paragraph.strip()):
            length = len(_squash(sentence))
            start, offset = offset, offset + length
            hit = any(min(offset, b) - max(start, a) >= min(MIN_OVERLAP_CHARS, length) for a, b in spans) or any(_same_sentence(c, sentence) for c in claims)
            if hit and length:
                removed += 1
            elif sentence.strip():
                kept.append(sentence.strip())
        if kept:
            paragraphs.append(' '.join(kept))
    return '\n\n'.join(paragraphs), removed


def _clean_list(items: Sequence[str], source: str, claims: Sequence[str]) -> tuple[list[str], int]:
    cleaned, removed = [], 0
    for item in items:
        text, n = _clean_text(item, quality.copied_spans(item, source), claims)
        removed += n
        if text:
            cleaned.append(text)
    return cleaned, removed


def _fact_sentences(draft: PostDraft) -> list[str]:
    """사실검증이 읽는 모든 칸(제목·소제목·용어사전 포함)의 문장. 걸린 문장이 어디 있었는지·남았는지 볼 때 쓴다."""
    fields = [draft.title, draft.thumbnail_text.replace('\n', ' '), draft.hook, *draft.key_points,
              *(s.heading for s in draft.sections), *(s.body for s in draft.sections), *(s.tip for s in draft.sections),
              draft.why_it_matters, *draft.action_items,
              *(f'{g.term}: {g.meaning}' for g in draft.glossary)]
    return [s for f in fields for para in _PARAGRAPH_SPLIT.split(f) for s in _SENTENCE_SPLIT.split(para.strip()) if s.strip()]


def _mentions(claims: Sequence[str], sentences: Sequence[str]) -> bool:
    return any(_same_sentence(c, s) for c in claims for s in sentences)


def remove_flagged(draft: PostDraft, article: Article, unsupported: Sequence[str]) -> PostDraft | None:
    """걸린 문장을 뺀 새 초안. 뺄 것이 없거나 제목·도입부 등 빼면 안 되는 곳이 걸리면 None."""
    # 제목·썸네일·소제목은 문장만 뺄 수 없다. 걸린 문장이 본문 어디에도 없으면(바꿔 쓴 인용) 뺐다고 말할 수 없다
    unmovable = [draft.title, draft.thumbnail_text.replace('\n', ' '), *(s.heading for s in draft.sections)]
    if _mentions(unsupported, unmovable):
        return None
    before = _fact_sentences(draft)
    if any(not _mentions([c], before) for c in unsupported):
        return None
    source, total = article.text, 0
    hook, n = _clean_text(draft.hook, quality.copied_spans(draft.hook, source), unsupported)
    total += n
    why, n = _clean_text(draft.why_it_matters, quality.copied_spans(draft.why_it_matters, source), unsupported)
    total += n
    key_points, n = _clean_list(draft.key_points, source, unsupported)
    total += n
    actions, n = _clean_list(draft.action_items, source, unsupported)
    total += n
    sections = []
    for s in draft.sections:
        body, n = _clean_text(s.body, quality.copied_spans(s.body, source), unsupported)
        tip, m = _clean_text(s.tip, quality.copied_spans(s.tip, source), unsupported)
        total += n + m
        if not body or not tip:
            return None
        sections.append(s.model_copy(update={'body': body, 'tip': tip}))
    glossary = [g for g in draft.glossary if not any(_same_sentence(c, f'{g.term}: {g.meaning}') for c in unsupported)]
    total += len(draft.glossary) - len(glossary)
    if not hook or not why or total == 0:
        return None
    fixed = draft.model_copy(update={
        'hook': hook, 'why_it_matters': why, 'key_points': key_points, 'action_items': actions,
        'sections': sections, 'glossary': glossary,
    })
    if _mentions(unsupported, _fact_sentences(fixed)):
        return None  # 걸린 문장이 어딘가에 남아 있으면 발행하지 않는다
    return fixed


def rescue(
    llm: LLMClient,
    fast_model: str,
    draft: PostDraft,
    article: Article,
    unsupported: Sequence[str],
    history: tuple[HistoryEntry, ...],
    is_alive: Callable[[str], bool],
    day: date | None = None,
) -> PostDraft | None:
    """걸린 문장을 빼고 검수를 다시 통과하면 그 초안을 돌려줍니다. 못 살리면 None."""
    for _ in range(MAX_PASSES):
        cleaned = remove_flagged(draft, article, unsupported)
        if cleaned is None:
            return None
        report = quality.run_quality_gate(llm, fast_model, cleaned, article, history, is_alive, day)
        if report.passed or report.only_soft:  # 문장을 빼서 분량이 모자라진 것은 발행을 막지 않는다
            return cleaned
        if not report.rescuable:
            return None
        draft, unsupported = cleaned, report.unsupported
    return None
