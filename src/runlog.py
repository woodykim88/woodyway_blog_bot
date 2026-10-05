"""실행 기록(data/runs.jsonl). 실행 1회 = 한 줄. 관리 화면의 '실행 기록'이 이 파일을 읽는다."""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_RUNS_PATH = Path(__file__).resolve().parent.parent / 'data' / 'runs.jsonl'
MAX_ERROR_CHARS = 500


@dataclass(frozen=True)
class PostRecord:
    title: str
    status: str
    slot: str
    url: str
    edit_url: str
    has_image: bool


@dataclass(frozen=True)
class RunRecord:
    started_at: str
    finished_at: str
    trigger: str        # schedule / workflow_dispatch / local
    mode: str           # auto / schedule / draft / now / preview
    outcome: str        # success / skipped / failed
    message: str
    run_id: str = ''
    run_url: str = ''
    text_provider: str = ''
    writer_model: str = ''
    image_model: str = ''
    llm_calls: int = 0
    posts: tuple[PostRecord, ...] = field(default_factory=tuple)
    usage: tuple[dict[str, Any], ...] = field(default_factory=tuple)  # 모델별 호출·토큰 (src/usage.py)
    stages: tuple[dict[str, Any], ...] = field(default_factory=tuple)  # 단계·모델별 호출·토큰 (선별·원고·윤문·검수)


def github_context(env: Mapping[str, str]) -> dict[str, str]:
    run_id = env.get('GITHUB_RUN_ID', '')
    server, repo = env.get('GITHUB_SERVER_URL', ''), env.get('GITHUB_REPOSITORY', '')
    return {
        'trigger': env.get('GITHUB_EVENT_NAME', 'local'),
        'run_id': run_id,
        'run_url': f'{server}/{repo}/actions/runs/{run_id}' if run_id and server and repo else '',
    }


def append_run(record: RunRecord, path: Path | None = None) -> None:
    path = path or DEFAULT_RUNS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    row = asdict(record)
    row['message'] = row['message'][:MAX_ERROR_CHARS]
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def parse_runs(text: str) -> list[dict[str, Any]]:
    """runs.jsonl 내용을 최신순 목록으로. 깨진 줄은 건너뜁니다."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(rows))
