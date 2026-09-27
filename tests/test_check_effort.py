"""검수(사실검증)만 GPT의 생각하는 양을 낮춘다 (2026-09-27 지혜 님 요청). 글쓰기는 모델 기본값 유지."""
import inspect
from types import SimpleNamespace

from pydantic import BaseModel

from src import providers, quality


class _Schema(BaseModel):
    ok: bool = True


class _Responses:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def parse(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(output_parsed=_Schema(), usage=None)


def test_reasoning_effort_only_for_models_that_accept_it():
    responses = _Responses()
    backend = providers.OpenAITextBackend(SimpleNamespace(responses=responses))
    backend.parse('gpt-5.4-mini', 'i', 'u', _Schema, effort='low')
    backend.parse('gpt-4.1-mini', 'i', 'u', _Schema, effort='low')
    backend.parse('gpt-5.6-terra', 'i', 'u', _Schema)
    assert [r.get('reasoning') for r in responses.requests] == [{'effort': 'low'}, None, None]


def test_fact_check_asks_for_low_effort():
    assert quality.CHECK_EFFORT == 'low'
    assert "load_prompt('factcheck'), user_input, FactCheck, effort=CHECK_EFFORT)" in inspect.getsource(quality)
