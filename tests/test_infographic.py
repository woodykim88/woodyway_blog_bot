import io

import pytest
from PIL import Image

from src import infographic, thumbnail
from src import main as pipeline
from tests.helpers import make_article, make_draft

FONT = thumbnail.find_font()
needs_font = pytest.mark.skipif(FONT is None, reason='굵은 한글 글꼴 없음')


def test_cards_for_builds_summary_and_checklist_with_limits(monkeypatch):
    monkeypatch.setattr(infographic, 'ENABLED_CARDS', ('summary', 'checklist'))
    draft = make_draft(key_points=['가', '나', '다', '라', '마', '바'], action_items=['오늘 할 일'])
    cards = {c.kind: c for c in infographic.cards_for(draft)}
    assert len(cards['summary'].items) == infographic.MAX_ITEMS
    assert cards['checklist'].items[0] == '오늘 할 일'  # 실천 항목이 꿀팁보다 먼저
    assert all(len(i) <= infographic.MAX_ITEM_CHARS for c in cards.values() for i in c.items)


def test_wrap_splits_long_text_without_losing_characters():
    font = thumbnail.load(FONT, 30) if FONT else None
    if font is None:
        pytest.skip('글꼴 없음')
    text = '띄어쓰기없이아주길게이어지는한국어문장도' * 5 + ' 마지막 단어'
    lines = infographic._wrap(text, font, 300)
    assert len(lines) > 1 and ''.join(lines).replace(' ', '') == text.replace(' ', '')
    assert all(font.getlength(line) <= 300 for line in lines)


@needs_font
def test_render_grows_with_items():
    short = infographic.render(infographic.Card('summary', '한눈에', ('하나',)), '검색어', (36, 76, 235), FONT)
    long = infographic.render(infographic.Card('checklist', '체크', ('하나', '둘', '셋', '넷')), '', (0, 150, 110), FONT)
    h1, h2 = (Image.open(io.BytesIO(b)).size for b in (short, long))
    assert h1[0] == h2[0] == infographic.WIDTH and h2[1] > h1[1]


@needs_font
def test_attach_infographics_dry_run_places_both_cards(tmp_path):
    composed = pipeline.Composed(make_article(), make_draft())
    result = pipeline.attach_infographics(composed, None, '2026-10-15', True, preview_dir=tmp_path)
    assert [k for k, _, _ in result.infographics] == ['summary']  # 10/15: 인포그래픽은 1장
    html = result.html
    assert html.index('info-summary.webp') < html.index('<h2>핵심 내용</h2>')
    assert 'info-checklist' not in html
    assert (tmp_path / 'info-summary.webp').exists()


def test_attach_infographics_skips_without_font_or_r2(tmp_path):
    composed = pipeline.Composed(make_article(), make_draft())
    assert pipeline.attach_infographics(composed, None, '2026-10-15', False) is composed
    assert pipeline.attach_infographics(composed, None, '2026-10-15', True, preview_dir=tmp_path,
                                        find_font=lambda: None) is composed
