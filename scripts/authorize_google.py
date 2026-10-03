"""Google 로그인을 다시 해서 블로그 + Search Console(읽기 전용) 권한이 함께 든 token.json을 만든다.

브라우저가 열리면 블로그 주인 Google 계정으로 로그인하고 두 권한을 모두 허용한다.
기존 token.json은 token.json.bak으로 남긴다. 토큰 값은 화면에 출력하지 않는다.
사용법: .venv/bin/python scripts/authorize_google.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent.parent
SCOPES = [
    'https://www.googleapis.com/auth/blogger',
    'https://www.googleapis.com/auth/webmasters.readonly',
]


def main() -> int:
    token_path = ROOT / 'token.json'
    if token_path.exists():
        shutil.copy(token_path, ROOT / 'token.json.bak')
    flow = InstalledAppFlow.from_client_secrets_file(str(ROOT / 'client_secret.json'), SCOPES)
    creds = flow.run_local_server(port=0, prompt='consent', access_type='offline')
    missing = [s for s in SCOPES if s not in (creds.scopes or [])]
    if missing:
        print(f'⚠️ 허용되지 않은 권한이 있습니다: {missing}\n브라우저에서 두 항목 모두 체크했는지 확인하고 다시 실행하세요.')
        return 1
    token_path.write_text(creds.to_json())
    print('✅ token.json 저장 (블로그 + Search Console 권한)')

    sites = build('searchconsole', 'v1', credentials=creds).sites().list().execute().get('siteEntry', [])
    if not sites:
        print('⚠️ 이 계정에 등록된 Search Console 사이트가 없습니다. search.google.com/search-console 에서 woodyway88.com을 먼저 추가하세요.')
    for s in sites:
        print(f"- {s['siteUrl']} ({s['permissionLevel']})")
    return 0


if __name__ == '__main__':
    sys.exit(main())
