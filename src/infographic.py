"""본문 인포그래픽: 글 내용으로 코드가 직접 그리는 요약 카드 (2026-10-15 사용자 지시).

AI 이미지 모델은 한글을 자주 깨뜨리므로, 글자가 들어가는 그림은 Pillow로 그린다. 이미지 API 비용이 없다.
- summary: "한눈에 보는 핵심" — key_points에 번호를 붙인 카드 (도입부 뒤)
- checklist: "바로 써먹는 체크리스트" — action_items + 섹션 꿀팁 체크박스 카드 (실천 항목 뒤)
글꼴이 없거나 항목이 비면 만들지 않는다. 그림 때문에 발행이 막히면 안 된다.
"""
from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from src import thumbnail
from src.models import PostDraft

WIDTH = 1000
PAD = 56
HEADER_H = 150
ITEM_GAP = 28
TEXT_SIZE = 31
TITLE_SIZE = 46
SUB_SIZE = 26
LINE_HEIGHT = 1.5
MARKER = 48               # 번호 원·체크박스 크기
TEXT_X = PAD + MARKER + 26
TEXT_COLOR = (31, 36, 48)
MAX_ITEMS = 5
MAX_ITEM_CHARS = 110      # 너무 긴 문장은 잘라서 카드가 끝없이 길어지지 않게
FOOTER = 'woodyway88.com'


@dataclass(frozen=True)
class Card:
    kind: str      # summary | checklist
    title: str
    items: tuple[str, ...]


def _mix(color: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    """color를 흰색과 섞는다 (ratio=0.9면 흰색 90%)."""
    return tuple(round(c + (255 - c) * ratio) for c in color)


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """한글은 띄어쓰기가 적어도 줄을 나눌 수 있게, 단어 단위로 넣다가 넘치면 글자 단위로 자른다."""
    lines: list[str] = []
    line = ''
    for word in text.split(' '):
        candidate = f'{line} {word}'.strip()
        if font.getlength(candidate) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = ''
        for ch in word:
            if font.getlength(line + ch) > width:
                lines.append(line)
                line = ''
            line += ch
    if line:
        lines.append(line)
    return lines


def _clip(text: str) -> str:
    text = ' '.join(text.split())
    return text if len(text) <= MAX_ITEM_CHARS else text[:MAX_ITEM_CHARS - 1].rstrip() + '…'


def cards_for(draft: PostDraft) -> list[Card]:
    summary = tuple(_clip(p) for p in draft.key_points if p.strip())[:MAX_ITEMS]
    todo = [a for a in draft.action_items if a.strip()] + [s.tip for s in draft.sections if s.tip.strip()]
    checklist = tuple(_clip(t) for t in todo)[:MAX_ITEMS]
    cards = []
    if summary:
        cards.append(Card('summary', '한눈에 보는 핵심', summary))
    if checklist:
        cards.append(Card('checklist', '바로 써먹는 체크리스트', checklist))
    return cards


def _draw_marker(draw: ImageDraw.ImageDraw, kind: str, index: int, x: int, y: int,
                 tint: tuple[int, int, int], font: ImageFont.FreeTypeFont) -> None:
    if kind == 'summary':
        draw.ellipse((x, y, x + MARKER, y + MARKER), fill=tint)
        draw.text((x + MARKER / 2, y + MARKER / 2), str(index), font=font, fill=(255, 255, 255), anchor='mm')
        return
    draw.rounded_rectangle((x, y, x + MARKER, y + MARKER), radius=10, outline=tint, width=4, fill=(255, 255, 255))
    draw.line([(x + 11, y + 25), (x + 21, y + 35), (x + 38, y + 13)], fill=tint, width=6, joint='curve')


def render(card: Card, subtitle: str, tint: tuple[int, int, int], font_spec: thumbnail.FontSpec) -> bytes:
    """카드 한 장을 PNG bytes로. 높이는 항목 길이에 맞춰 늘어난다."""
    body_font = thumbnail.load(font_spec, TEXT_SIZE)
    title_font = thumbnail.load(font_spec, TITLE_SIZE)
    sub_font = thumbnail.load(font_spec, SUB_SIZE)
    marker_font = thumbnail.load(font_spec, 26)
    step = round(TEXT_SIZE * LINE_HEIGHT)
    wrapped = [_wrap(item, body_font, WIDTH - TEXT_X - PAD) for item in card.items]
    blocks = [max(len(lines) * step, MARKER) for lines in wrapped]
    height = HEADER_H + PAD + sum(blocks) + ITEM_GAP * (len(blocks) - 1) + PAD + 50

    image = Image.new('RGB', (WIDTH, height), _mix(tint, 0.93))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, HEADER_H), fill=tint)
    draw.text((PAD, 44), card.title, font=title_font, fill=(255, 255, 255), anchor='lt')
    if subtitle:
        draw.text((PAD, 44 + TITLE_SIZE + 14), subtitle, font=sub_font, fill=_mix(tint, 0.75), anchor='lt')

    y = HEADER_H + PAD
    for index, (lines, block) in enumerate(zip(wrapped, blocks), start=1):
        _draw_marker(draw, card.kind, index, PAD, y, tint, marker_font)
        text_top = y + max(0, (MARKER - step) // 2)
        for n, line in enumerate(lines):
            draw.text((TEXT_X, text_top + n * step), line, font=body_font, fill=TEXT_COLOR, anchor='lt')
        y += block + ITEM_GAP
    draw.text((WIDTH - PAD, height - 36), FOOTER, font=sub_font, fill=_mix(tint, 0.35), anchor='rm')

    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()
