"""PostDraft를 Blogger용 HTML로 렌더링한다. LLM 출력은 모두 자동 이스케이프된다."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

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


def render_post(
    draft: PostDraft,
    article: Article,
    short_news: Sequence[ShortNews] = (),
    related: Sequence[HistoryEntry] = (),
    hero_image_url: str | None = None,
    footer_url: str | None = None,
) -> str:
    return _env.get_template('post.html.j2').render(
        draft=draft,
        sections=[(s.heading, split_paragraphs(s.body)) for s in draft.sections],
        source_url=article.source_url,
        geeknews_url=article.candidate.geeknews_url,
        short_news=short_news,
        related=related,
        hero_image_url=hero_image_url,
        footer_url=footer_url,
    )
