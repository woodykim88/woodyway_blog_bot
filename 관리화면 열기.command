#!/bin/zsh
# 더블클릭하면 정보마스터 관리 화면이 브라우저에서 열립니다. 창을 닫으면 관리 화면도 꺼집니다.
cd "$(dirname "$0")"
echo "정보마스터 관리 화면을 여는 중... (이 창을 닫으면 관리 화면이 꺼집니다)"
exec .venv/bin/streamlit run ui/app.py
