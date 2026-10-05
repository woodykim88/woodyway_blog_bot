"""글자 수만 넘친 제목·메타 설명·썸네일 문구는 원고를 다시 쓰지 않고 그 칸만 값싼 모델로 고친다.

검수에서 "썸네일 15자(14자 이내)"처럼 한 칸만 걸려도 원고 모델이 글 전체를 다시 썼다. 최근 발행 글의 절반 가까이가
이렇게 두 번 써졌다. 고친 값이 규칙을 못 지키면 원래 초안을 그대로 두고, 그 뒤는 평소처럼 검수가 판단한다.
"""
from __future__ import annotations

from src import quality
from src.llm import LLMClient, load_prompt
from src.models import FittedFields, PostDraft


def _has_keyword(text: str, keyword: str) -> bool:
    return bool(keyword) and keyword.replace(' ', '') in text.replace(' ', '')


def _clean_words(text: str) -> bool:
    return not any(w in text for w in quality.BANNED_WORDS)


def _title_ok(title: str, keyword: str) -> bool:
    return (0 < len(title) <= quality.MAX_TITLE_CHARS and _has_keyword(title, keyword)
            and not quality.FORMULAIC_TITLE.search(title) and _clean_words(title))


def _meta_ok(meta: str, keyword: str) -> bool:
    return 0 < len(meta) <= quality.MAX_META_CHARS and _has_keyword(meta, keyword) and _clean_words(meta)


def _problems(draft: PostDraft) -> dict[str, str]:
    """글자 수 때문에 걸리는 칸과 이유. 키워드가 없는 것처럼 글자 수 문제가 아닌 건 여기서 다루지 않는다."""
    found: dict[str, str] = {}
    if len(draft.title) > quality.MAX_TITLE_CHARS:
        found['title'] = f'현재 {len(draft.title)}자 → {quality.MAX_TITLE_CHARS}자 이내로'
    if len(draft.meta_description) > quality.MAX_META_CHARS:
        found['meta_description'] = f'현재 {len(draft.meta_description)}자 → {quality.MAX_META_CHARS}자 이내로'
    if issues := quality.check_thumbnail(draft):
        found['thumbnail'] = ' / '.join(issues)
    return found


def _build_input(draft: PostDraft, problems: dict[str, str]) -> str:
    flagged = '\n'.join(f'- {name}: {why}' for name, why in problems.items())
    return '\n'.join([
        f'검색 키워드: {draft.search_keyword}',
        '', '고칠 칸:', flagged,
        '', '현재 값:',
        f'title: {draft.title}',
        f'meta_description: {draft.meta_description}',
        f'thumbnail_text: {draft.thumbnail_text}',
        f'thumbnail_highlight: {draft.thumbnail_highlight}',
        '', f'참고(도입부): {draft.hook}',
    ])


def fit_fields(llm: LLMClient, model: str, draft: PostDraft) -> PostDraft:
    """걸린 칸만 고친 새 초안. 걸린 칸이 없거나 고치지 못하면 원래 초안."""
    problems = _problems(draft)
    if not problems:
        return draft
    try:
        result = llm.parse(model, load_prompt('fit_fields'), _build_input(draft, problems), FittedFields, stage='fit_fields')
    except Exception as e:  # noqa: BLE001 - 선택 단계다. API 오류로 발행이 막히면 안 되니 원래 초안으로 계속한다
        print(f'[칸 수정 건너뜀] {e.__class__.__name__}: {str(e)[:100]}')
        return draft
    updates: dict[str, str] = {}
    keyword = draft.search_keyword.strip()
    if 'title' in problems and _title_ok(result.title.strip(), keyword):
        updates['title'] = result.title.strip()
    if 'meta_description' in problems and _meta_ok(result.meta_description.strip(), keyword):
        updates['meta_description'] = result.meta_description.strip()
    if 'thumbnail' in problems:
        candidate = draft.model_copy(update={
            'thumbnail_text': result.thumbnail_text.strip(), 'thumbnail_highlight': result.thumbnail_highlight.strip()})
        if not quality.check_thumbnail(candidate):
            updates.update(thumbnail_text=candidate.thumbnail_text, thumbnail_highlight=candidate.thumbnail_highlight)
    if updates:
        print(f'[칸 수정] 원고는 그대로 두고 {", ".join(updates)}만 고쳤습니다')
    return draft.model_copy(update=updates)
