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
    if article.references:
        parts.append(
            f'원문 본문 뒤에 같은 소식을 다룬 관련 기사 {len(article.references)}건이 이어 붙어 있습니다. '
            '한 기사를 고쳐 쓰지 말고, 기사마다 다른 숫자·관점·빠진 정보를 비교·대조해 한 편으로 엮으세요. '
            '기사끼리 다른 내용은 "한 기사는 ~, 다른 기사는 ~"처럼 밝히고, 주제와 상관없는 기사 내용은 쓰지 마세요.'
        )
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
