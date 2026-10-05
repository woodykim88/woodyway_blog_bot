from types import SimpleNamespace

from src import usage
from src.llm import LLMClient
from src.models import FactCheck
from src.providers import OpenAITextBackend
from tests.helpers import FakeOpenAI


def test_usage_sums_tokens_per_model_and_survives_missing_usage():
    usage.reset()
    usage.record('gpt-a', SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20)))
    usage.record('gpt-a', SimpleNamespace(usage=SimpleNamespace(input_tokens=50, output_tokens=5)))
    usage.record('img', SimpleNamespace())
    assert usage.snapshot() == (
        {'model': 'gpt-a', 'calls': 2, 'input_tokens': 150, 'output_tokens': 25, 'cached_tokens': 0},
        {'model': 'img', 'calls': 1, 'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0},
    )
    usage.reset()
    assert usage.snapshot() == ()
    assert usage.stage_snapshot() == ()


def test_usage_counts_cached_input_tokens_from_either_api_shape():
    usage.reset()
    responses_api = SimpleNamespace(usage=SimpleNamespace(
        input_tokens=1000, output_tokens=10, input_tokens_details=SimpleNamespace(cached_tokens=800)))
    chat_api = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=500, completion_tokens=5, prompt_tokens_details=SimpleNamespace(cached_tokens=100)))
    usage.record('gpt-a', responses_api)
    usage.record('gpt-a', chat_api)
    assert usage.snapshot()[0]['cached_tokens'] == 900
    usage.reset()


def test_usage_splits_by_stage_and_defaults_to_empty_stage():
    usage.reset()
    resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=2))
    with usage.stage('write'):
        usage.record('big', resp)
        usage.record('big', resp)
    with usage.stage('factcheck'):
        usage.record('small', resp)
    usage.record('small', resp)
    rows = {(r['stage'], r['model']): r for r in usage.stage_snapshot()}
    assert rows[('write', 'big')]['calls'] == 2 and rows[('write', 'big')]['input_tokens'] == 20
    assert rows[('factcheck', 'small')]['calls'] == 1
    assert rows[('', 'small')]['calls'] == 1
    usage.reset()


def test_llm_parse_labels_calls_with_its_stage():
    usage.reset()
    fake = FakeOpenAI({'FactCheck': [FactCheck(claims=[])]})
    original = fake.responses.parse
    fake.responses.parse = lambda **k: (lambda r: (setattr(r, 'usage', SimpleNamespace(input_tokens=7, output_tokens=1)), r)[1])(original(**k))
    llm = LLMClient(OpenAITextBackend(fake))
    llm.parse('m', 'inst', 'in', FactCheck, stage='factcheck')
    assert [(r['stage'], r['model'], r['input_tokens']) for r in usage.stage_snapshot()] == [('factcheck', 'm', 7)]
    usage.reset()
