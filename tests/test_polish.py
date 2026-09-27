"""윤문: 초안 문장만 사람이 다듬은 글처럼 고치고, 사실·제목·구성은 그대로 둔다."""
from pathlib import Path

from src import polish
from src.models import PolishedProse, Section
from tests.helpers import fake_llm, make_draft

ROOT = Path(__file__).resolve().parent.parent


def polished_for(draft, **overrides):
    fields = {
        'hook': '다듬은 도입부입니다.\n\n두 번째 문단이죠.',
        'key_points': [f'다듬은 핵심 {i}' for i in range(len(draft.key_points))],
        'sections': [Section(heading='바뀌면 안 되는 소제목', body=f'다듬은 본문 {i}', tip=f'다듬은 꿀팁 {i}')
                     for i in range(len(draft.sections))],
        'why_it_matters': '다듬은 의미입니다.',
        'action_items': [f'다듬은 행동 {i}' for i in range(len(draft.action_items))],
    }
    return PolishedProse(**{**fields, **overrides})


def test_polish_replaces_only_prose_and_keeps_facts_frame():
    draft = make_draft()
    llm, client = fake_llm({'PolishedProse': [polished_for(draft)]})
    result = polish.polish_draft(llm, 'writer', draft)
    assert result.hook.startswith('다듬은 도입부') and result.why_it_matters == '다듬은 의미입니다.'
    assert result.key_points[0] == '다듬은 핵심 0' and result.action_items[0] == '다듬은 행동 0'
    assert result.sections[0].body == '다듬은 본문 0' and result.sections[0].tip == '다듬은 꿀팁 0'
    # 제목·검색어·소제목·썸네일·라벨 등은 원래 값 그대로
    assert result.sections[0].heading == draft.sections[0].heading
    for field in ('title', 'search_keyword', 'labels', 'meta_description', 'thumbnail_text', 'glossary', 'image_prompt'):
        assert getattr(result, field) == getattr(draft, field)
    sent = client.requests[0]['input']
    assert draft.hook.split('\n')[0] in sent and draft.title not in sent  # 제목은 보내지 않는다 (바꿀 대상 아님)


def test_polish_keeps_original_when_llm_fails():
    draft = make_draft()
    llm, _ = fake_llm({})  # 윤문 응답 없음 → 실패
    assert polish.polish_draft(llm, 'writer', draft) == draft


def test_polish_keeps_original_when_shape_changes():
    draft = make_draft()
    fewer = polished_for(draft, sections=[Section(heading='h', body='b', tip='t')] * (len(draft.sections) - 1))
    empty = polished_for(draft, hook='  ')
    for bad in (fewer, empty):
        llm, _ = fake_llm({'PolishedProse': [bad]})
        assert polish.polish_draft(llm, 'writer', draft) == draft


def test_polish_prompt_bans_invented_experience_and_ai_cliches():
    prompt = (ROOT / 'prompts' / 'polish.md').read_text(encoding='utf-8')
    assert '지어내' in prompt and '알아보겠습니다' in prompt and '숫자' in prompt


def test_post_template_has_no_ai_notice():
    template = (ROOT / 'templates' / 'post.html.j2').read_text(encoding='utf-8')
    assert 'AI의 도움' not in template and 'AI가 작성' not in template
