"""OpenAI 구조화 응답 호출 래퍼. 1회 실행당 호출 횟수에 상한을 둔다(비용 폭주 방지)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

PROMPTS_DIR = Path(__file__).resolve().parent.parent / 'prompts'
DEFAULT_MAX_CALLS = 16  # 최악의 경우 1 + 3주제 x 2회 x (작성+검증) = 13회

T = TypeVar('T', bound=BaseModel)


class LLMError(Exception):
    """LLM 호출 실패 또는 상한 초과."""


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f'{name}.md').read_text(encoding='utf-8')


class LLMClient:
    def __init__(self, client: Any, max_calls: int = DEFAULT_MAX_CALLS) -> None:
        self._client = client
        self._max_calls = max_calls
        self.calls = 0

    def parse(self, model: str, instructions: str, user_input: str, schema: type[T]) -> T:
        """schema 형태의 응답을 받아 검증된 pydantic 객체로 반환합니다."""
        if self.calls >= self._max_calls:
            raise LLMError(f'이번 실행의 LLM 호출 상한({self._max_calls}회)을 넘었습니다.')
        self.calls += 1
        response = self._client.responses.parse(
            model=model,
            instructions=instructions,
            input=user_input,
            text_format=schema,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise LLMError(f'{schema.__name__} 응답을 해석하지 못했습니다 (거절 또는 형식 오류).')
        return parsed
