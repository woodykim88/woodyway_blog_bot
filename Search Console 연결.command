#!/bin/zsh
# 더블클릭: Google 로그인(블로그 + Search Console 권한) → GitHub 비밀값 갱신
cd "$(dirname "$0")"
.venv/bin/python scripts/authorize_google.py || { echo "\n중단됨. 창을 닫아도 됩니다."; read; exit 1; }
.venv/bin/python scripts/set_github_secrets.py
echo "\n완료. 창을 닫아도 됩니다."
read
