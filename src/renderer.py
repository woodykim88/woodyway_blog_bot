"""PostDraft를 Blogger용 HTML로 렌더링한다. LLM 출력은 모두 자동 이스케이프된다."""
from __future__ import annotations

from collections.abc import Sequence
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


MONEY_NOTE = '※ 일반적인 정보이며, 세금·대출·혜택은 개인 상황에 따라 다를 수 있어요. 자세한 조건은 원문과 해당 기관에서 확인하세요.'


def render_post(
    draft: PostDraft,
    article: Article,
    short_news: Sequence[ShortNews] = (),
    related: Sequence[HistoryEntry] = (),
    hero_image_url: str | None = None,
    footer_url: str | None = None,
    tool: Tool | None = None,
    skill: Skill | None = None,
) -> str:
    return _env.get_template('post.html.j2').render(
        draft=draft,
        hook_paragraphs=split_paragraphs(draft.hook),
        sections=[(s.heading, split_paragraphs(s.body), s.tip) for s in draft.sections],
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
