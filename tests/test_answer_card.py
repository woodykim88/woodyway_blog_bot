"""정답 카드 (2026-10-22): 글 맨 위에 조건 → 큰 숫자 → 근거 한 줄. 사람과 AI 검색이 답을 바로 뽑아 가게."""
from src import quality, renderer
from src.models import PostDraft
from src.tools import Tool
from tests.helpers import make_article, make_draft

ANSWER = {
    'answer_condition': '월급 300만 원 · 고용보험 3년 · 50세 미만',
    'answer_value': '약 1,188만 원',
    'answer_detail': '하루 66,048원 × 180일',
}
BODY = '하루 66,048원씩 180일을 받아 총 약 1,188만 원입니다. ' * 20
TOOL = Tool(title='실업급여 계산기', url='https://tools.woodyway88.com/u/', keywords=('실업급여',), blurb='내 조건으로 계산')


def draft_with(**overrides) -> PostDraft:
    base = make_draft()
    section = base.sections[0].model_copy(update={'body': BODY})
    return make_draft(**{'sections': [section, *base.sections[1:]], **ANSWER, **overrides})


def test_answer_fields_default_empty_but_required_for_llm():
    assert make_draft().answer_value == ''
    required = PostDraft.model_json_schema()['required']
    assert {'answer_condition', 'answer_value', 'answer_detail'} <= set(required)


def test_answer_card_sits_at_top_before_hook():
    html = renderer.render_post(draft_with(), make_article())
    card = html.index('class="im-answer"')
    assert card < html.index('class="im-lead"')
    for text in ANSWER.values():
        assert text in html


def test_answer_card_holds_calculator_button_instead_of_separate_box():
    html = renderer.render_post(draft_with(), make_article(), tool=TOOL)
    card = html[html.index('class="im-answer"'):html.index('class="im-lead"')]
    assert TOOL.url in card and 'class="im-btn"' in card
    assert html.count(TOOL.url) == 2  # 카드 안 1번 + 글 끝 1번
    assert '내 경우는 얼마일까?' not in html


def test_no_card_without_value():
    html = renderer.render_post(draft_with(answer_value=''), make_article())
    assert 'im-answer' not in html


def test_no_card_when_number_not_in_body():
    """본문 어디에도 없는 숫자면 지어낸 값일 수 있어 카드를 숨긴다."""
    html = renderer.render_post(draft_with(answer_value='약 2,000만 원'), make_article())
    assert 'im-answer' not in html


def test_answer_supported_ignores_spacing_and_units():
    draft = draft_with()
    assert renderer.answer_supported(draft)
    assert renderer.answer_supported(draft.model_copy(update={'answer_value': '1188만원'}))
    assert not renderer.answer_supported(draft.model_copy(update={'answer_value': '1,189만 원'}))


def test_fact_check_includes_answer():
    text = quality.fact_check_text(draft_with())
    assert '약 1,188만 원' in text and '하루 66,048원 × 180일' in text
