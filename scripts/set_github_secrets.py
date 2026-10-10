"""로컬 .env, client_secret.json, token.json의 값을 GitHub Actions Secrets로 등록한다.

값은 화면에 출력하지 않고 gh CLI의 표준 입력으로만 전달한다.
사용법: .venv/bin/python scripts/set_github_secrets.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_KEYS = (
    'BLOG_ID', 'OPENAI_API_KEY', 'GPTPROTO_API_KEY',
    'R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET', 'R2_PUBLIC_BASE_URL',
    'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID',
)


def collect() -> dict[str, str]:
    env = dotenv_values(ROOT / '.env')
    values = {key: (env.get(key) or '').strip() for key in ENV_KEYS}

    client = json.loads((ROOT / 'client_secret.json').read_text())
    client = client.get('installed') or client.get('web') or {}
    token = json.loads((ROOT / 'token.json').read_text())
    values['GOOGLE_CLIENT_ID'] = client.get('client_id', '')
    values['GOOGLE_CLIENT_SECRET'] = client.get('client_secret', '')
    values['GOOGLE_REFRESH_TOKEN'] = token.get('refresh_token', '')
    return values


def main() -> int:
    values = collect()
    for name, value in values.items():
        if not value:
            print(f'[건너뜀] {name}: 값이 비어 있음')
            continue
        subprocess.run(['gh', 'secret', 'set', name], input=value, text=True, check=True, cwd=ROOT,
                       stdout=subprocess.DEVNULL)
        print(f'[등록] {name} ({len(value)}자)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
