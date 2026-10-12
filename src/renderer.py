"""PostDraft를 Blogger용 HTML로 렌더링한다. LLM 출력은 모두 자동 이스케이프된다."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from src import topics
from src.skills import Skill
from src.tools import Tool
from src.history import HistoryEntry
from src.models import Article, PostDraft, ShortNews

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / 'templates'

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=True,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split('\n\n') if p.strip()]


_STEP = re.compile(r'^\s*\d{1,2}\s*[.)]\s+')
_QUESTION = re.compile(r'^\s*Q\s*[.:)]\s*')
_ANSWER = re.compile(r'^\s*A\s*[.:)]\s*')


@dataclass(frozen=True)
class Block:
    """섹션 본문 한 덩어리: 문단(p), 번호 목록(ol, 줄마다 '1. '), 자주 묻는 질문(q, 'Q. '로 시작 → h3)."""

    kind: str
    text: str = ''
    items: tuple[str, ...] = ()


def to_blocks(body: str) -> list[Block]:
    """문단마다 번호 목록·질문·답을 알아본다. 생활 가이드 글의 단계별 방법과 FAQ를 목록·h3로 보여 주려는 것."""
    blocks: list[Block] = []
    for paragraph in split_paragraphs(body):
        lines = [x.strip() for x in paragraph.splitlines() if x.strip()]
        if len(lines) >= 2 and all(_STEP.match(x) for x in lines):
            blocks.append(Block('ol', items=tuple(_STEP.sub('', x) for x in lines)))
        elif _QUESTION.match(lines[0]):
            blocks.append(Block('q', text=_QUESTION.sub('', lines[0])))
            if len(lines) > 1:
                blocks.append(Block('p', text=_ANSWER.sub('', ' '.join(lines[1:]))))
        else:
            blocks.append(Block('p', text=_ANSWER.sub('', paragraph) if _ANSWER.match(paragraph) else paragraph))
    return blocks


def as_of_note(as_of: str) -> str:
    if not as_of:
        return ''
    return (f'※ 이 글은 {as_of} 기준 공식 안내를 바탕으로 썼어요. 신청 기간·금액·조건은 해마다 바뀔 수 있으니 '
            '신청 전에 아래 공식 출처에서 최신 내용을 꼭 확인하세요.')


MONEY_NOTE = '※ 일반적인 정보이며, 세금·대출·혜택은 개인 상황에 따라 다를 수 있어요. 자세한 조건은 원문과 해당 기관에서 확인하세요.'


def body_image_slots(section_count: int, image_count: int) -> list[int]:
    """본문 그림을 붙일 섹션 번호(그 섹션 뒤에 놓는다). 한 장이면 가운데쯤, 두 장이면 처음과 세 번째 섹션 뒤."""
    last = max(section_count - 1, 0)
    wanted = [1] if image_count == 1 else [0, 2, 3][:image_count]
    return [min(i, last) for i in wanted]


def render_post(
    draft: PostDraft,
    article: Article,
    short_news: Sequence[ShortNews] = (),
    related: Sequence[HistoryEntry] = (),
    hero_image_url: str | None = None,
    body_images: Sequence[tuple[str, str]] = (),
    footer_url: str | None = None,
    tool: Tool | None = None,
    skill: Skill | None = None,
    as_of: str = '',
) -> str:
    """as_of(기준 시점)가 있으면 생활 가이드 글: 기준 시점 안내를 붙이고 출처를 '공식 안내'로 표시한다."""
    slots = body_image_slots(len(draft.sections), len(body_images))
    body_image_at = dict(zip(slots, body_images))
    return _env.get_template('post.html.j2').render(
        draft=draft,
        hook_paragraphs=split_paragraphs(draft.hook),
        sections=[(s.heading, to_blocks(s.body), s.tip, body_image_at.get(i)) for i, s in enumerate(draft.sections)],
        as_of_note=as_of_note(as_of),
        source_prefix='공식 안내' if as_of else '원문',
        reference_prefix='함께 본 공식 안내' if as_of else '함께 본 기사',
        source_url=article.source_url,
        source_name=article.candidate.source,
        references=article.references,
        # GeekNews처럼 다른 곳에서 원문을 찾아온 경우에만 "via" 링크를 단다
        via_url=None if article.candidate.direct else article.candidate.link,
        short_news=short_news,
        related=related,
        hero_image_url=hero_image_url,
        footer_url=footer_url,
        tool=tool,
        skill=skill,
        money_note=MONEY_NOTE if topics.MONEY_LABELS & set(draft.labels) else '',
    )
