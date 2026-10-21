"""대표 이미지 왼쪽에 굵은 썸네일 문구를 얹는다 (Pillow).

2026-10-22 B안(에디토리얼): 그림은 그대로 두고, 검은 테두리·판화 그림자가 있는 종이색 상자 안에 먹색 글자를 쓴다.
강조 단어는 레몬색 상자, 라벨은 먹색 꼬리표. 사이트 테마(theme/jungbomaster-theme.xml)의 색과 같다.

검색 결과·피드에서 이미지만 보고도 무슨 글인지 알고 클릭하게 만드는 것이 목적이다.
글꼴이 없거나 문구가 비어 있으면 원본 이미지를 그대로 돌려준다. 썸네일 문구 때문에 발행이 막히면 안 된다.
"""
from __future__ import annotations

import io
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

CANVAS = (1200, 800)
MARGIN_X = 60
MAX_TEXT_WIDTH = round(CANVAS[0] * 0.52)
START_SIZE = 110
MIN_SIZE = 56
LINE_HEIGHT = 1.18
STROKE_RATIO = 0.022     # 강조 단어: 같은 색 테두리로 한 단계 더 굵게 (Noto Bold → Black 느낌)
SIZE_STEP = 4
INK_COLOR = (22, 22, 22)
PAPER_COLOR = (243, 238, 227)
TEXT_COLOR = INK_COLOR
HIGHLIGHT_COLOR = (244, 210, 60, 255)  # 레몬 (테마 --lemon)
BOX_PAD_X = 34
BOX_PAD_Y = 28
BOX_BORDER = 6
BOX_SHADOW = 14
LABEL_SIZE = 30
LABEL_TOP = 56
BOX_TOP = 56                 # 라벨이 없을 때 상자 윗변
BOX_TOP_WITH_LABEL = 128     # 라벨 꼬리표 아래
REFERENCE_GLYPHS = '가힣Ag2'  # 강조 상자 높이를 단어와 상관없이 일정하게 맞추는 기준 글자


@dataclass(frozen=True)
class FontSpec:
    path: str
    index: int = 0  # .ttc 안의 글꼴 번호 (Noto CJK: 1=KR)


# 분야별 왼쪽 오버레이 색: 검정 대신 선명한 색으로 밝고 눈에 띄게 (2026-10-13). 흰 글씨가 읽히는 중간 명도.
TINTS: dict[str, tuple[int, int, int]] = {
    'tech': (36, 76, 235),    # 선명한 파랑
    'money': (0, 150, 110),   # 선명한 초록
    'skill': (112, 64, 224),  # 보라
    'guide': (112, 64, 224),
    'howto': (235, 100, 40),  # 주황
}
DEFAULT_TINT = TINTS['tech']


def tint_for(category: str) -> tuple[int, int, int]:
    return TINTS.get(category, DEFAULT_TINT)


@dataclass(frozen=True)
class Headline:
    text: str             # 2줄 문구, 줄바꿈 1개
    highlight: str = ''   # 노란 형광펜으로 칠할 단어
    label: str = ''       # 왼쪽 위 먹색 꼬리표 (블로그 라벨)


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


def headline_lines(text: str) -> list[str]:
    """줄 목록. 모델이 줄바꿈을 글자 그대로의 \\n으로 보내는 경우도 줄로 나눈다 (2026-10-22)."""
    return [line.strip() for line in text.replace('\\n', '\n').split('\n') if line.strip()]


def _glyph_box(font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    """기준선(baseline) 대비 글자 위·아래 끝."""
    _, top, _, bottom = font.getbbox(REFERENCE_GLYPHS, anchor='ls')
    return top, bottom


def _stroke(font: ImageFont.FreeTypeFont) -> int:
    return round(font.size * STROKE_RATIO)


def _baselines(font: ImageFont.FreeTypeFont, count: int, block_top: int) -> list[int]:
    """글 묶음 맨 윗글자가 block_top에 오도록 각 줄의 기준선 y를 구합니다.

    2026-10-22: 가운데 두면 풍자 일러스트의 주인공(가운데·아래 1/3)을 가려서 왼쪽 위로 올렸다.
    """
    step = round(font.size * LINE_HEIGHT)
    top, _ = _glyph_box(font)
    return [block_top - top + i * step for i in range(count)]


def _draw_caption_box(canvas: Image.Image, lines: Sequence[str], font: ImageFont.FreeTypeFont,
                      baselines: Sequence[int]) -> None:
    """글 묶음을 감싸는 종이색 상자 + 먹색 테두리 + 오른쪽 아래 판화 그림자."""
    top, bottom = _glyph_box(font)
    width = max(font.getlength(line) for line in lines) + round(font.size * 0.2)
    box = (MARGIN_X - BOX_PAD_X, baselines[0] + top - BOX_PAD_Y,
           MARGIN_X + width + BOX_PAD_X, baselines[-1] + bottom + BOX_PAD_Y)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(tuple(v + BOX_SHADOW for v in box), fill=INK_COLOR)
    draw.rectangle(box, fill=PAPER_COLOR, outline=INK_COLOR, width=BOX_BORDER)


def _draw_line(canvas: Image.Image, line: str, highlight: str, font: ImageFont.FreeTypeFont, y: int) -> None:
    before, marked, after = split_highlight(line, highlight)
    x = MARGIN_X
    draw = ImageDraw.Draw(canvas)
    draw.text((x, y), before, font=font, anchor='ls', fill=TEXT_COLOR)
    x += font.getlength(before)
    if marked:
        top, bottom = _glyph_box(font)
        pad_x, pad_y = round(font.size * 0.1), round(font.size * 0.03)
        width = font.getlength(marked)
        draw.rectangle((x - pad_x / 2, y + top - pad_y, x + width + pad_x, y + bottom + pad_y), fill=HIGHLIGHT_COLOR)
        stroke = round(font.size * STROKE_RATIO)
        draw.text((x + pad_x / 4, y), marked, font=font, anchor='ls', fill=TEXT_COLOR,
                  stroke_width=stroke, stroke_fill=TEXT_COLOR)
        x += width + pad_x * 1.25
    draw.text((x, y), after, font=font, anchor='ls', fill=TEXT_COLOR)


def _draw_label(draw: ImageDraw.ImageDraw, label: str, font_spec: FontSpec) -> None:
    font = load(font_spec, LABEL_SIZE)
    pad_x, pad_y = 18, 10
    x0 = MARGIN_X - BOX_PAD_X
    _, _, right, bottom = draw.textbbox((x0 + pad_x, LABEL_TOP + pad_y), label, font=font, anchor='lt')
    draw.rectangle((x0, LABEL_TOP, right + pad_x, bottom + pad_y), fill=INK_COLOR)
    draw.text((x0 + pad_x, LABEL_TOP + pad_y), label, font=font, anchor='lt', fill=HIGHLIGHT_COLOR[:3])


def render(image: Image.Image, headline: Headline, font_spec: FontSpec) -> Image.Image:
    """CANVAS 크기로 맞춘 뒤 종이 상자 → 문구 → 라벨 순서로 그립니다. 그림 자체는 덮지 않습니다."""
    lines = headline_lines(headline.text)
    font = fit_font(font_spec, lines)
    block_top = (BOX_TOP_WITH_LABEL if headline.label.strip() else BOX_TOP) + BOX_PAD_Y
    baselines = _baselines(font, len(lines), block_top)
    canvas = cover(image).convert('RGBA')
    _draw_caption_box(canvas, lines, font, baselines)
    for line, y in zip(lines, baselines):
        _draw_line(canvas, line, headline.highlight.strip(), font, y)
    if headline.label.strip():
        _draw_label(ImageDraw.Draw(canvas), headline.label.strip(), font_spec)
    return canvas.convert('RGB')


def add_headline(data: bytes, headline: Headline, font: FontSpec | None) -> bytes:
    """이미지 bytes에 썸네일 문구를 얹어 PNG bytes로 돌려줍니다. 못 얹으면 원본 그대로."""
    if not headline_lines(headline.text):
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


# --- 루이 마스코트 (2026-09-30 AI·IT·스킬 글에만 → 2026-10-21 사용자 결정으로 중단: 3D풍이 새 일러스트 스타일과 어긋남) ---
# 루이 채널(@louis.ai.code) 캐릭터 시트에서 오린 투명 PNG. 그림을 새로 만들지 않아 매번 같은 모습이고 비용이 없다.
MASCOT_DIR = Path(__file__).resolve().parent.parent / 'assets' / 'louis'
MASCOTS: dict[str, str] = {}  # 다시 쓰려면 {'skill': 'louis-laptop.png', 'guide': 'louis-laptop.png', 'tech': 'louis-front.png'}
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
