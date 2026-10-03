"""대표 이미지 왼쪽에 굵은 썸네일 문구를 얹는다 (Pillow).

검색 결과·피드에서 이미지만 보고도 무슨 글인지 알고 클릭하게 만드는 것이 목적이다.
글꼴이 없거나 문구가 비어 있으면 원본 이미지를 그대로 돌려준다. 썸네일 문구 때문에 발행이 막히면 안 된다.
"""
from __future__ import annotations

import io
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

CANVAS = (1200, 800)
MARGIN_X = 60
MAX_TEXT_WIDTH = round(CANVAS[0] * 0.52)
START_SIZE = 110
MIN_SIZE = 56
LINE_HEIGHT = 1.18
STROKE_RATIO = 0.022     # 같은 색 테두리로 글자를 한 단계 더 굵게 (Noto Bold → Black 느낌)
SIZE_STEP = 4
GRADIENT_ALPHA = 0.9       # 맨 왼쪽 검정 농도
GRADIENT_END = 0.65        # 이 비율 지점에서 그라데이션이 0이 된다
TEXT_COLOR = (255, 255, 255)
HIGHLIGHT_COLOR = (255, 214, 10, 128)  # 노랑 50%: 위 줄 글자와 겹쳐도 비쳐 보이게 (인스타 표지와 같은 스타일)
HIGHLIGHT_TEXT_COLOR = TEXT_COLOR
SHADOW_OFFSET = 5
SHADOW_BLUR = 6
SHADOW_ALPHA = 200
LABEL_COLOR = '#E63946'
LABEL_SIZE = 32
LABEL_TOP = 64
REFERENCE_GLYPHS = '가힣Ag2'  # 강조 상자 높이를 단어와 상관없이 일정하게 맞추는 기준 글자


@dataclass(frozen=True)
class FontSpec:
    path: str
    index: int = 0  # .ttc 안의 글꼴 번호 (Noto CJK: 1=KR)


@dataclass(frozen=True)
class Headline:
    text: str             # 2줄 문구, 줄바꿈 1개
    highlight: str = ''   # 노란 형광펜으로 칠할 단어
    label: str = ''       # 왼쪽 위 빨간 알약 (블로그 라벨)


FONT_CANDIDATES: tuple[FontSpec, ...] = (
    FontSpec('/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc', 1),
    FontSpec('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc', 1),
    FontSpec('/usr/share/fonts/noto-cjk/NotoSansCJK-Black.ttc', 1),
    FontSpec('/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc', 1),
    FontSpec('/usr/share/fonts/google-noto-cjk/NotoSansCJK-Black.ttc', 1),
    FontSpec('/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc', 1),
    FontSpec('/System/Library/Fonts/AppleSDGothicNeo.ttc', 16),  # 16 = Heavy
)


def find_font(
    candidates: Sequence[FontSpec] = FONT_CANDIDATES, exists: Callable[[str], bool] = os.path.exists
) -> FontSpec | None:
    """설치된 굵은 한글 글꼴 중 첫 번째를 고릅니다. 없으면 None."""
    return next((c for c in candidates if exists(c.path)), None)


def load(font: FontSpec, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font.path, size, index=font.index)


def fit_font(font: FontSpec, lines: Sequence[str]) -> ImageFont.FreeTypeFont:
    """가장 긴 줄이 MAX_TEXT_WIDTH 안에 들어올 때까지 글자 크기를 줄입니다."""
    size = START_SIZE
    while True:
        loaded = load(font, size)
        if size <= MIN_SIZE or max(loaded.getlength(line) for line in lines) <= MAX_TEXT_WIDTH:
            return loaded
        size = max(MIN_SIZE, size - SIZE_STEP)


def split_highlight(line: str, highlight: str) -> tuple[str, str, str]:
    """줄을 (앞, 강조, 뒤)로 나눕니다. 강조 단어가 없으면 줄 전체가 앞부분입니다."""
    if not highlight or highlight not in line:
        return line, '', ''
    before, _, after = line.partition(highlight)
    return before, highlight, after


def cover(image: Image.Image, size: tuple[int, int] = CANVAS) -> Image.Image:
    """비율이 다르면 가운데를 잘라 size를 꽉 채웁니다 (16:9 → 3:2)."""
    return ImageOps.fit(image.convert('RGB'), size, Image.LANCZOS)


def darken_left(image: Image.Image) -> Image.Image:
    """왼쪽은 검정 90%, GRADIENT_END 지점에서 0이 되는 그라데이션을 덮습니다."""
    width, height = image.size
    end = max(1, round(width * GRADIENT_END))
    ramp = [round(255 * GRADIENT_ALPHA * max(0.0, 1 - x / end) ** 1.3) for x in range(width)]
    mask = Image.new('L', (width, 1))
    mask.putdata(ramp)
    black = Image.new('RGB', image.size, (0, 0, 0))
    return Image.composite(black, image, mask.resize(image.size))


def _glyph_box(font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    """기준선(baseline) 대비 글자 위·아래 끝."""
    _, top, _, bottom = font.getbbox(REFERENCE_GLYPHS, anchor='ls')
    return top, bottom


def _stroke(font: ImageFont.FreeTypeFont) -> int:
    return round(font.size * STROKE_RATIO)


def _baselines(font: ImageFont.FreeTypeFont, count: int, height: int) -> list[int]:
    """여러 줄 묶음이 세로 가운데에 오도록 각 줄의 기준선 y를 구합니다."""
    step = round(font.size * LINE_HEIGHT)
    top, bottom = _glyph_box(font)
    block = step * (count - 1) + (bottom - top)
    first = round((height - block) / 2 - top)
    return [first + i * step for i in range(count)]


def _draw_shadow(image: Image.Image, lines: Sequence[str], font: ImageFont.FreeTypeFont,
                 baselines: Sequence[int]) -> Image.Image:
    layer = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for line, y in zip(lines, baselines):
        draw.text((MARGIN_X + SHADOW_OFFSET, y + SHADOW_OFFSET), line, font=font, anchor='ls',
                  fill=(0, 0, 0, SHADOW_ALPHA), stroke_width=_stroke(font), stroke_fill=(0, 0, 0, SHADOW_ALPHA))
    layer = layer.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    return Image.alpha_composite(image, layer)


def _paint_box(canvas: Image.Image, box: tuple[float, float, float, float], radius: int) -> None:
    """반투명 상자는 별도 층에 그려 합성합니다 (RGBA 캔버스에 바로 칠하면 섞이지 않고 구멍이 난다)."""
    layer = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, fill=HIGHLIGHT_COLOR)
    canvas.alpha_composite(layer)


def _draw_line(canvas: Image.Image, line: str, highlight: str, font: ImageFont.FreeTypeFont, y: int) -> None:
    before, marked, after = split_highlight(line, highlight)
    x = MARGIN_X
    stroke = _stroke(font)
    draw = ImageDraw.Draw(canvas)
    draw.text((x, y), before, font=font, anchor='ls', fill=TEXT_COLOR, stroke_width=stroke, stroke_fill=TEXT_COLOR)
    x += font.getlength(before)
    if marked:
        top, bottom = _glyph_box(font)
        pad_x, pad_y = round(font.size * 0.14), round(font.size * 0.03)
        width = font.getlength(marked)
        _paint_box(canvas, (x - pad_x / 2, y + top - pad_y, x + width + pad_x, y + bottom + pad_y),
                   round(font.size * 0.14))
        draw = ImageDraw.Draw(canvas)
        draw.text((x + pad_x / 4, y), marked, font=font, anchor='ls', fill=HIGHLIGHT_TEXT_COLOR,
                  stroke_width=stroke, stroke_fill=HIGHLIGHT_TEXT_COLOR)
        x += width + pad_x * 1.25
    draw.text((x, y), after, font=font, anchor='ls', fill=TEXT_COLOR, stroke_width=stroke, stroke_fill=TEXT_COLOR)


def _draw_label(draw: ImageDraw.ImageDraw, label: str, font_spec: FontSpec) -> None:
    font = load(font_spec, LABEL_SIZE)
    pad_x, pad_y = 22, 12
    _, top, right, bottom = draw.textbbox((MARGIN_X + pad_x, LABEL_TOP + pad_y), label, font=font, anchor='lt')
    draw.rounded_rectangle((MARGIN_X, LABEL_TOP, right + pad_x, bottom + pad_y), radius=(bottom - top + pad_y * 2) // 2,
                           fill=LABEL_COLOR)
    draw.text((MARGIN_X + pad_x, LABEL_TOP + pad_y), label, font=font, anchor='lt', fill=TEXT_COLOR)


def render(image: Image.Image, headline: Headline, font_spec: FontSpec) -> Image.Image:
    """CANVAS 크기로 맞춘 뒤 그라데이션 → 그림자 → 문구 → 라벨 순서로 그립니다."""
    lines = [line.strip() for line in headline.text.strip().split('\n') if line.strip()]
    font = fit_font(font_spec, lines)
    baselines = _baselines(font, len(lines), CANVAS[1])
    canvas = _draw_shadow(darken_left(cover(image)).convert('RGBA'), lines, font, baselines)
    for line, y in zip(lines, baselines):
        _draw_line(canvas, line, headline.highlight.strip(), font, y)
    if headline.label.strip():
        _draw_label(ImageDraw.Draw(canvas), headline.label.strip(), font_spec)
    return canvas.convert('RGB')


def add_headline(data: bytes, headline: Headline, font: FontSpec | None) -> bytes:
    """이미지 bytes에 썸네일 문구를 얹어 PNG bytes로 돌려줍니다. 못 얹으면 원본 그대로."""
    if not headline.text.strip():
        print('[썸네일 문구 생략] 문구가 비어 있어 이미지만 씁니다.')
        return data
    if font is None:
        print('[썸네일 문구 생략] 굵은 한글 글꼴을 찾지 못했습니다. (fonts-noto-cjk 설치 확인)')
        return data
    try:
        with Image.open(io.BytesIO(data)) as source:
            result = render(source, headline, font)
    except (OSError, ValueError) as e:
        print(f'[썸네일 문구 실패] {e.__class__.__name__}: {e} → 문구 없이 이미지만 씁니다.')
        return data
    buffer = io.BytesIO()
    result.save(buffer, format='PNG')
    return buffer.getvalue()


# --- 루이 마스코트 (AI·IT·스킬 글에만, 2026-09-30 사용자 결정) ---
# 루이 채널(@louis.ai.code) 캐릭터 시트에서 오린 투명 PNG. 그림을 새로 만들지 않아 매번 같은 모습이고 비용이 없다.
MASCOT_DIR = Path(__file__).resolve().parent.parent / 'assets' / 'louis'
MASCOTS = {'skill': 'louis-laptop.png', 'guide': 'louis-laptop.png', 'tech': 'louis-front.png'}
MASCOT_HEIGHT_RATIO = 0.42   # 이미지 높이 대비 루이 키
MASCOT_RIGHT_MARGIN = 0.02   # 오른쪽 여백 (이미지 너비 대비)


def mascot_for(category: str) -> Path | None:
    name = MASCOTS.get(category)
    return MASCOT_DIR / name if name else None


def add_mascot(data: bytes, mascot: Path | None) -> bytes:
    """이미지 오른쪽 아래에 루이를 얹어 PNG bytes로. 제목이 있는 왼쪽은 건드리지 않는다. 못 얹으면 원본 그대로."""
    if mascot is None:
        return data
    try:
        with Image.open(io.BytesIO(data)) as source, Image.open(mascot) as louis:
            canvas = source.convert('RGBA')
            height = round(canvas.height * MASCOT_HEIGHT_RATIO)
            width = round(louis.width * height / louis.height)
            sprite = louis.convert('RGBA').resize((width, height), Image.LANCZOS)
            x = canvas.width - width - round(canvas.width * MASCOT_RIGHT_MARGIN)
            canvas.alpha_composite(sprite, (x, canvas.height - height))
    except (OSError, ValueError) as e:
        print(f'[루이 생략] {e.__class__.__name__}: {e} → 루이 없이 이미지만 씁니다.')
        return data
    buffer = io.BytesIO()
    canvas.convert('RGB').save(buffer, format='PNG')
    return buffer.getvalue()
