#!/bin/zsh
# 매주 월요일 08:30 (launchd com.woody.weekly-report): 최신 Search Console·애드센스 자료를 받아 주간 성적표를 텔레그램으로 보낸다.
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
{
  echo "[$(date '+%F %T')] 시작"
  git pull -q --rebase --autostash || echo "git pull 실패, 가진 자료로 보냄"
  .venv/bin/python -m src.adsense >/dev/null || echo "애드센스 갱신 실패, 지난 자료로 보냄"
  .venv/bin/python -m src.weekly_report
  echo "[$(date '+%F %T')] 끝 (종료 코드 $?)"
} >> logs/weekly_report.log 2>&1
