"""원문을 바탕으로 직장인 관점의 글 초안(PostDraft)을 생성한다."""
from __future__ import annotations

from collections.abc import Sequence

from src import topics
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


def instructions(category: str = topics.TECH) -> str:
    """분야별 필자 설정(누구로서, 누구에게, 어떤 라벨) + 공통 글쓰기 규칙."""
    return f"{load_prompt(topics.SPECS[category].persona_prompt).strip()}\n\n{load_prompt('writer')}"


def write_post(
    llm: LLMClient, model: str, article: Article, theme: str, feedback: Sequence[str] = (),
    category: str = topics.TECH,
) -> PostDraft:
    return llm.parse(model, instructions(category), build_writer_input(article, theme, feedback), PostDraft)
