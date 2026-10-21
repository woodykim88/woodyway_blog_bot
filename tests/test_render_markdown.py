from src import renderer


def test_md_bold_becomes_strong():
    """2026-10-22: 모델이 쓴 **강조**가 글에 별표 그대로 찍혔다."""
    assert str(renderer.md('실제 금액은 하루 **66,048원**입니다.')) == '실제 금액은 하루 <strong>66,048원</strong>입니다.'


def test_md_escapes_html_before_bold():
    assert str(renderer.md('<b>x</b> **<i>y</i>**')) == '&lt;b&gt;x&lt;/b&gt; <strong>&lt;i&gt;y&lt;/i&gt;</strong>'


def test_md_leaves_lone_asterisks():
    assert str(renderer.md('별표 * 하나와 ** 짝 없는 것')) == '별표 * 하나와 ** 짝 없는 것'


def test_rendered_post_has_no_raw_double_asterisks():
    from tests.helpers import make_draft, make_article
    draft = make_draft()
    section = draft.sections[0].model_copy(update={'body': '하루 **66,048원** × 180일'})
    draft = draft.model_copy(update={'sections': [section, *draft.sections[1:]], 'hook': '총 **1,188만 원**입니다.'})
    html = renderer.render_post(draft, make_article())
    assert '**' not in html
    assert '<strong>66,048원</strong>' in html and '<strong>1,188만 원</strong>' in html


def test_rendered_post_carries_meta_description_marker():
    """2026-10-22: API로 검색 설명을 못 넣어서, 다음 날 편집기 단계(도구/색인요청/describe.py)가 읽을 표시를 남긴다."""
    from tests.helpers import make_draft, make_article
    html = renderer.render_post(make_draft(meta_description='회의록 AI 요약 --> 1분 <b>'), make_article())
    assert html.startswith('<!-- im-meta: 회의록 AI 요약 --&gt; 1분 &lt;b&gt; -->')


def test_faq_jsonld_from_question_blocks():
    import json
    import re
    from tests.helpers import make_draft, make_article
    draft = make_draft()
    faq = 'Q. 언제 받나요?\n\nA. 9월 말까지 **지급**됩니다.\n\nQ. 누가 받나요?\n\nA. 가입 1년 이상인 사람입니다.'
    section = draft.sections[2].model_copy(update={'body': faq})
    html = renderer.render_post(draft.model_copy(update={'sections': [*draft.sections[:2], section]}), make_article())
    data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
    assert data['@type'] == 'FAQPage'
    assert [q['name'] for q in data['mainEntity']] == ['언제 받나요?', '누가 받나요?']
    assert data['mainEntity'][0]['acceptedAnswer']['text'] == '9월 말까지 지급됩니다.'


def test_no_faq_jsonld_without_questions():
    from tests.helpers import make_draft, make_article
    assert 'FAQPage' not in renderer.render_post(make_draft(), make_article())
