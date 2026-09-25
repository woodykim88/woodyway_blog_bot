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
    tmp.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
    os.chmod(tmp, 0o600)
    tmp.replace(path)
