"""이번 실행의 API 사용량 집계. AI 응답의 usage(토큰 수)를 모델별·단계별로 더해 실행 기록(data/runs.jsonl)에 남긴다.

대시보드의 "API 사용량" 화면이 채널별 평균 사용량·예상 비용을 이 기록으로 계산한다.
- usage: 모델별 합계 (호출·입력·출력·캐시된 입력 토큰). 대시보드 비용 계산의 기준.
- stages: (단계, 모델)별 합계. 선별·원고·윤문·검수 중 어디서 토큰이 나가는지 볼 때 쓴다.
한 번 실행하고 끝나는 프로세스라 모듈 안에 합계를 둔다 (실행마다 새로 시작).
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_FIELDS = ('calls', 'input_tokens', 'output_tokens', 'cached_tokens')

_totals: dict[str, dict[str, int]] = {}
_stage_totals: dict[tuple[str, str], dict[str, int]] = {}
_stage: ContextVar[str] = ContextVar('usage_stage', default='')


def _tokens(usage: Any, *names: str) -> int:
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, int):
            return value
    return 0


def _cached_tokens(usage: Any) -> int:
    """입력 토큰 중 OpenAI가 캐시로 처리해 싸게 받은 몫 (Responses API·Chat API 모양 모두)."""
    details = getattr(usage, 'input_tokens_details', None) or getattr(usage, 'prompt_tokens_details', None)
    return _tokens(details, 'cached_tokens')


@contextmanager
def stage(name: str) -> Iterator[None]:
    """이 블록 안에서 기록되는 호출에 단계 이름(select·write·polish·factcheck 등)을 붙인다."""
    token = _stage.set(name)
    try:
        yield
    finally:
        _stage.reset(token)


def _add(row: dict[str, int] | None, delta: dict[str, int]) -> dict[str, int]:
    base = row or dict.fromkeys(_FIELDS, 0)
    return {k: base[k] + delta[k] for k in _FIELDS}


def record(model: str, response: Any, stage_name: str | None = None) -> None:
    """응답에 usage가 없으면(테스트용 가짜 응답 등) 호출 횟수만 셉니다. 기록 실패가 작업을 막으면 안 된다."""
    usage = getattr(response, 'usage', None)
    delta = {
        'calls': 1,
        'input_tokens': _tokens(usage, 'input_tokens', 'prompt_tokens'),
        'output_tokens': _tokens(usage, 'output_tokens', 'completion_tokens'),
        'cached_tokens': _cached_tokens(usage),
    }
    _totals[model] = _add(_totals.get(model), delta)
    key = (_stage.get() if stage_name is None else stage_name, model)
    _stage_totals[key] = _add(_stage_totals.get(key), delta)


def snapshot() -> tuple[dict[str, Any], ...]:
    return tuple({'model': model, **row} for model, row in sorted(_totals.items()))


def stage_snapshot() -> tuple[dict[str, Any], ...]:
    return tuple({'stage': s, 'model': model, **row} for (s, model), row in sorted(_stage_totals.items()))


def reset() -> None:
    _totals.clear()
    _stage_totals.clear()
