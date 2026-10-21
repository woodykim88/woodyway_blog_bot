from dataclasses import replace
import io

import pytest
from PIL import Image

from src import thumbnail
from src.models import PostDraft
from tests.helpers import make_draft

FONT = thumbnail.find_font()
needs_font = pytest.mark.skipif(FONT is None, reason='한글 글꼴이 없는 환경')
HEADLINE = thumbnail.Headline('코파일럿\n24시간 일한다?', '24시간', 'AI활용')


def solid(width=1536, height=1024, color=(40, 90, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buffer, format='WEBP', quality=90)
    return buffer.getvalue()


def opened(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as im:
        return im.convert('RGB')


def pixels(im: Image.Image) -> list[tuple[int, int, int]]:
    raw = im.tobytes()
    return [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]


# --- 모델 기본값 ---

def test_post_draft_thumbnail_fields_default_to_empty():
    fields = make_draft().model_dump(exclude={'thumbnail_text', 'thumbnail_highlight'})
    draft = PostDraft(**fields)
    assert draft.thumbnail_text == '' and draft.thumbnail_highlight == ''


def test_post_draft_schema_requires_thumbnail_fields_for_llm():
    schema = PostDraft.model_json_schema()
    assert {'thumbnail_text', 'thumbnail_highlight'} <= set(schema['required'])
    assert 'default' not in schema['properties']['thumbnail_text']


# --- 글꼴 찾기 ---

def test_find_font_returns_first_existing_candidate():
    candidates = (thumbnail.FontSpec('/nope/a.ttc', 1), thumbnail.FontSpec('/yes/b.ttc', 2))
    found = thumbnail.find_font(candidates, exists=lambda p: p == '/yes/b.ttc')
    assert found == thumbnail.FontSpec('/yes/b.ttc', 2)


def test_find_font_none_when_nothing_exists():
    assert thumbnail.find_font(thumbnail.FONT_CANDIDATES, exists=lambda p: False) is None


def test_font_candidates_prefer_heavy_noto_then_mac():
    paths = [c.path for c in thumbnail.FONT_CANDIDATES]
    assert paths[0] == '/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc'
    assert paths[1] == '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'
    assert paths[-1] == '/System/Library/Fonts/AppleSDGothicNeo.ttc'


# --- 문구 얹기 ---

def test_add_headline_without_font_returns_original(capsys):
    data = solid()
    assert thumbnail.add_headline(data, HEADLINE, None) is data
    assert '글꼴' in capsys.readouterr().out


def test_add_headline_with_empty_text_returns_original():
    data = solid()
    assert thumbnail.add_headline(data, thumbnail.Headline('  '), FONT) is data


def test_add_headline_bad_font_path_returns_original(capsys):
    data = solid()
    assert thumbnail.add_headline(data, HEADLINE, thumbnail.FontSpec('/nope/missing.ttc')) is data
    assert '썸네일' in capsys.readouterr().out


def caption_pixels(headline: thumbnail.Headline, color=(40, 90, 200)) -> list[tuple[int, int, int]]:
    return pixels(opened(thumbnail.add_headline(solid(color=color), headline, FONT)))


def near(p, q, tol=12) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(p, q))


@needs_font
def test_add_headline_keeps_picture_clean_outside_caption_box():
    """2026-10-22 B안: 그라데이션을 깔지 않고 그림을 그대로 둔다. 글은 종이색 상자 안에만."""
    im = opened(thumbnail.add_headline(solid(), HEADLINE, FONT))
    assert im.size == thumbnail.CANVAS
    assert im.getpixel((10, 790)) == pytest.approx((40, 90, 200), abs=8)
    assert im.getpixel((1180, 790)) == pytest.approx((40, 90, 200), abs=8)


@needs_font
def test_caption_box_is_paper_with_ink_border_and_shadow():
    ps = caption_pixels(thumbnail.Headline('월 3만원\n아끼는 설정'))
    paper = sum(1 for p in ps if near(p, thumbnail.PAPER_COLOR, 6))
    ink = sum(1 for p in ps if near(p, thumbnail.INK_COLOR, 20))
    assert paper > 60000
    assert ink > 15000  # 테두리 + 판화 그림자 + 글자


@needs_font
def test_caption_text_is_ink_not_white():
    ps = caption_pixels(thumbnail.Headline('월 3만원\n아끼는 설정'), color=(0, 0, 0))
    assert sum(1 for p in ps if min(p) > 240) < 500


def test_headline_lines_splits_literal_backslash_n():
    """2026-10-22: 모델이 줄바꿈을 글자 '\\n'으로 보내 썸네일에 그대로 찍혔다."""
    assert thumbnail.headline_lines('파손 발견\\n먼저 할 일') == ['파손 발견', '먼저 할 일']
    assert thumbnail.headline_lines(' 3년 뒤 \n\n 약 881만원 ') == ['3년 뒤', '약 881만원']


@needs_font
def test_add_headline_crops_wide_image_to_3_2():
    out = thumbnail.add_headline(solid(1408, 768), HEADLINE, FONT)
    assert opened(out).size == (1200, 800)


@needs_font
def test_add_headline_paints_lemon_highlight_box():
    lemon = lambda p: near(p, thumbnail.HIGHLIGHT_COLOR[:3], 14)
    assert sum(1 for p in caption_pixels(HEADLINE) if lemon(p)) > 3000
    plain = thumbnail.Headline('코파일럿\n24시간 일한다?', '없는말')
    assert sum(1 for p in caption_pixels(plain) if lemon(p)) == 0


@needs_font
def test_label_is_ink_tag_with_lemon_text():
    with_label = sum(1 for p in caption_pixels(HEADLINE) if near(p, thumbnail.INK_COLOR, 20))
    without = sum(1 for p in caption_pixels(replace(HEADLINE, label='')) if near(p, thumbnail.INK_COLOR, 20))
    assert with_label - without > 4000


@needs_font
def test_fit_font_keeps_each_line_within_text_width():
    font = thumbnail.fit_font(FONT, ('아주 길고 긴 문구가 들어간 한 줄',))
    assert font.getlength('아주 길고 긴 문구가 들어간 한 줄') <= thumbnail.MAX_TEXT_WIDTH or font.size == thumbnail.MIN_SIZE
    assert thumbnail.fit_font(FONT, ('짧게',)).size == thumbnail.START_SIZE


def test_split_highlight():
    assert thumbnail.split_highlight('배터리 2배', '2배') == ('배터리 ', '2배', '')
    assert thumbnail.split_highlight('배터리 2배', '') == ('배터리 2배', '', '')
    assert thumbnail.split_highlight('배터리 2배', '3배') == ('배터리 2배', '', '')
