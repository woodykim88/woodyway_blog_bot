"""로컬 .env 파일 읽기/쓰기. 다른 줄과 주석은 그대로 두고 지정한 키만 바꾼다."""
from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / '.env'


def read_env(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_PATH
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


def update_env(updates: dict[str, str], path: Path | None = None) -> None:
    path = path or ENV_PATH
    lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    remaining = dict(updates)
    new_lines = []
    for line in lines:
        key = line.split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
        if key in remaining:
            new_lines.append(f'{key}={remaining.pop(key)}')
        else:
            new_lines.append(line)
    new_lines.extend(f'{k}={v}' for k, v in remaining.items())
    tmp = path.with_suffix('.tmp')
    # 처음부터 본인만 읽을 수 있는 권한(600)으로 만든다 (쓰고 나서 권한을 바꾸면 잠깐 노출될 수 있음)
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write('\n'.join(new_lines) + '\n')
    os.chmod(tmp, 0o600)  # 이미 있던 .tmp 파일이면 권한을 다시 고정
    tmp.replace(path)
