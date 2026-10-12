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


@needs_font
def test_add_headline_renders_cover_size_and_darkens_left():
    out = thumbnail.add_headline(solid(), HEADLINE, FONT)
    im = opened(out)
    assert im.size == thumbnail.CANVAS
    left, right = im.getpixel((20, 790)), im.getpixel((1180, 790))
    assert left == pytest.approx(thumbnail.DEFAULT_TINT, abs=12)  # 왼쪽은 분야 색 오버레이가 덮인다
    assert right == pytest.approx((40, 90, 200), abs=6)  # 오른쪽 주제 영역은 그대로


@needs_font
def test_add_headline_crops_wide_image_to_3_2():
    out = thumbnail.add_headline(solid(1408, 768), HEADLINE, FONT)
    assert opened(out).size == (1200, 800)


@needs_font
def test_add_headline_paints_solid_yellow_highlight_box():
    def count(headline, test):
        black = replace(headline, tint=(0, 0, 0))  # 오버레이 색이 노랑 판별을 흐리지 않게 검정으로
        im = opened(thumbnail.add_headline(solid(color=(0, 0, 0)), black, FONT))
        return sum(1 for p in pixels(im) if test(*p))

    solid_yellow = lambda r, g, b: r > 230 and g > 190 and b < 60
    assert count(HEADLINE, solid_yellow) > 5000  # 강조 단어 뒤에 선명한 노랑 상자 (2026-10-13 불투명으로 변경)
    assert count(thumbnail.Headline('코파일럿\n24시간 일한다?', '없는말'), solid_yellow) == 0


@needs_font
def test_add_headline_draws_white_text_on_left():
    im = opened(thumbnail.add_headline(solid(color=(0, 0, 0)), thumbnail.Headline('월 3만원\n아끼는 설정'), FONT))
    text_area = im.crop((0, 0, round(im.width * 0.6), im.height))
    whites = sum(1 for p in pixels(text_area) if min(p) > 230)
    assert whites > 5000


@needs_font
def test_fit_font_keeps_each_line_within_text_width():
    font = thumbnail.fit_font(FONT, ('아주 길고 긴 문구가 들어간 한 줄',))
    assert font.getlength('아주 길고 긴 문구가 들어간 한 줄') <= thumbnail.MAX_TEXT_WIDTH or font.size == thumbnail.MIN_SIZE
    assert thumbnail.fit_font(FONT, ('짧게',)).size == thumbnail.START_SIZE


def test_split_highlight():
    assert thumbnail.split_highlight('배터리 2배', '2배') == ('배터리 ', '2배', '')
    assert thumbnail.split_highlight('배터리 2배', '') == ('배터리 2배', '', '')
    assert thumbnail.split_highlight('배터리 2배', '3배') == ('배터리 2배', '', '')
