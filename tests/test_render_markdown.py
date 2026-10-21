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
