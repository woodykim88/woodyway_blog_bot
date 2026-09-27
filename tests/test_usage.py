from types import SimpleNamespace

from src import usage


def test_usage_sums_tokens_per_model_and_survives_missing_usage():
    usage.reset()
    usage.record('gpt-a', SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20)))
    usage.record('gpt-a', SimpleNamespace(usage=SimpleNamespace(input_tokens=50, output_tokens=5)))
    usage.record('img', SimpleNamespace())
    assert usage.snapshot() == (
        {'model': 'gpt-a', 'calls': 2, 'input_tokens': 150, 'output_tokens': 25},
        {'model': 'img', 'calls': 1, 'input_tokens': 0, 'output_tokens': 0},
    )
    usage.reset()
    assert usage.snapshot() == ()
