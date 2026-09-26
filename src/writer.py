"""원문을 바탕으로 직장인 관점의 글 초안(PostDraft)을 생성한다."""
from __future__ import annotations

from collections.abc import Sequence

from src.llm import LLMClient, load_prompt
from src.models import Article, PostDraft


def build_writer_input(article: Article, theme: str, feedback: Sequence[str] = ()) -> str:
    parts = [
        f'오늘의 테마: {theme}',
        f'원문 출처: {article.candidate.source}',
        f'원문 URL: {article.source_url}',
        f'원문 제목(참고): {article.candidate.title}',
        '--- 원문 본문 시작 ---',
        article.text,
        '--- 원문 본문 끝 ---',
    ]
    if feedback:
        parts.append('이전 초안이 검수에서 아래 이유로 탈락했습니다. 반드시 고쳐서 다시 작성하세요:')
        parts.extend(f'- {item}' for item in feedback)
    return '\n'.join(parts)


def write_post(
    llm: LLMClient, model: str, article: Article, theme: str, feedback: Sequence[str] = ()
) -> PostDraft:
    return llm.parse(model, load_prompt('writer'), build_writer_input(article, theme, feedback), PostDraft)
