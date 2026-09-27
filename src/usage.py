"""이번 실행의 API 사용량 집계. AI 응답의 usage(토큰 수)를 모델별로 더해 실행 기록(data/runs.jsonl)에 남긴다.

대시보드의 "API 사용량" 화면이 채널별 평균 사용량·예상 비용을 이 기록으로 계산한다.
한 번 실행하고 끝나는 프로세스라 모듈 안에 합계를 둔다 (실행마다 새로 시작).
"""
from __future__ import annotations

from typing import Any

_totals: dict[str, dict[str, int]] = {}


def _tokens(usage: Any, *names: str) -> int:
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, int):
            return value
    return 0


def record(model: str, response: Any) -> None:
    """응답에 usage가 없으면(테스트용 가짜 응답 등) 호출 횟수만 셉니다. 기록 실패가 작업을 막으면 안 된다."""
    usage = getattr(response, 'usage', None)
    row = _totals.get(model, {'calls': 0, 'input_tokens': 0, 'output_tokens': 0})
    _totals[model] = {
        'calls': row['calls'] + 1,
        'input_tokens': row['input_tokens'] + _tokens(usage, 'input_tokens', 'prompt_tokens'),
        'output_tokens': row['output_tokens'] + _tokens(usage, 'output_tokens', 'completion_tokens'),
    }


def snapshot() -> tuple[dict[str, Any], ...]:
    return tuple({'model': model, **row} for model, row in sorted(_totals.items()))


def reset() -> None:
    _totals.clear()
