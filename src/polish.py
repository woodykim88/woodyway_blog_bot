"""윤문: 초안의 문장만 사람이 다듬은 글처럼 고친다. 사실·숫자·제목·소제목·구성은 그대로 둔다.

- 사용자 요청(2026-09-27): AI 티가 나지 않도록, 사람이 쓴 것처럼 윤문한 문체.
- 윤문은 선택 단계다. 실패하거나 구성이 달라지면 원래 초안을 그대로 쓴다 (발행을 막지 않는다).
- 사실검증은 윤문한 결과로 한다 (윤문 중에 사실이 바뀌어도 검수에서 걸린다).
"""
from __future__ import annotations

import json

from src.llm import LLMClient, LLMError, load_prompt
from src.models import PolishedProse, PostDraft


def build_polish_input(draft: PostDraft) -> str:
    """윤문할 문장만 보낸다. 제목·검색어·썸네일은 보내지 않는다 (바꿀 대상이 아니다)."""
    prose = {
        'hook': draft.hook,
        'key_points': draft.key_points,
        'sections': [s.model_dump() for s in draft.sections],
        'why_it_matters': draft.why_it_matters,
        'action_items': draft.action_items,
    }
    return '--- 초안 (JSON) ---\n' + json.dumps(prose, ensure_ascii=False, indent=2)


def _same_shape(draft: PostDraft, polished: PolishedProse) -> bool:
    texts = [polished.hook, polished.why_it_matters, *polished.key_points, *polished.action_items,
             *(s.body for s in polished.sections), *(s.tip for s in polished.sections)]
    return (len(polished.sections) == len(draft.sections) and len(polished.key_points) == len(draft.key_points)
            and len(polished.action_items) == len(draft.action_items) and all(t.strip() for t in texts))


def polish_draft(llm: LLMClient, model: str, draft: PostDraft) -> PostDraft:
    """다듬은 문장으로 바꾼 새 초안. 실패하면 원래 초안."""
    try:
        polished = llm.parse(model, load_prompt('polish'), build_polish_input(draft), PolishedProse, stage='polish')
    except (LLMError, ValueError) as e:
        print(f'[윤문 건너뜀] {e.__class__.__name__}: {str(e)[:100]}')
        return draft
    if not _same_shape(draft, polished):
        print('[윤문 건너뜀] 구성이 초안과 달라 원래 문장을 씁니다')
        return draft
    sections = [old.model_copy(update={'body': new.body.strip(), 'tip': new.tip.strip()})
                for old, new in zip(draft.sections, polished.sections)]
    print('[윤문] 문장을 다듬었습니다')
    return draft.model_copy(update={
        'hook': polished.hook.strip(),
        'key_points': [p.strip() for p in polished.key_points],
        'sections': sections,
        'why_it_matters': polished.why_it_matters.strip(),
        'action_items': [a.strip() for a in polished.action_items],
    })
