#!/bin/zsh
# 더블클릭하면 정보마스터 관리 화면이 브라우저에서 열립니다. 이 창을 닫으면 관리 화면도 꺼집니다.
cd "$(dirname "$0")"
URL="http://127.0.0.1:8501"

if curl -s -m 2 "$URL/_stcore/health" >/dev/null; then
  echo "관리 화면이 이미 켜져 있습니다. 브라우저로 엽니다."
  open "$URL"
  exit 0
fi

echo "정보마스터 관리 화면을 여는 중... (이 창을 닫으면 관리 화면이 꺼집니다)"
.venv/bin/streamlit run ui/app.py &
SERVER_PID=$!
for i in {1..30}; do
  curl -s -m 1 "$URL/_stcore/health" >/dev/null && break
  sleep 1
done
open "$URL"
wait $SERVER_PID
