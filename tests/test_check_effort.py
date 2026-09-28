"""GPT 생각하는 양 옵션. 사실검증은 9/28부터 다시 모델 기본값 (low는 후킹 문장까지 탈락시켜 재작성이 늘었다)."""
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


def test_fact_check_uses_default_effort():
    assert quality.CHECK_EFFORT is None
    assert "load_prompt('factcheck'), user_input, FactCheck, effort=CHECK_EFFORT)" in inspect.getsource(quality)
