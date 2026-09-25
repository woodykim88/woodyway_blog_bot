"""GitHub Actions 사전 점검: 지금 준비할 발행 슬롯이 있는지 판단한다 (외부 라이브러리 없이 동작).

결과를 GITHUB_OUTPUT에 due=true/false로 기록한다. 준비할 게 없으면 무거운 설치 단계를 건너뛴다.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import history, schedule


def main() -> int:
    paused = (os.environ.get('PAUSED') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    try:
        times = schedule.parse_times(os.environ.get('PUBLISH_TIMES'))
    except schedule.ScheduleError as e:
        print(f'[설정 오류] {e} → 본 실행에서 오류를 보고하도록 due=true')
        due = True
    else:
        slots = schedule.due_slots(datetime.now(timezone.utc), times, history.done_slots(history.load_history()))
        due = bool(slots) and not paused
        print(f'[점검] 발행 시각 {schedule.format_times(times)}, 일시정지={paused}, '
              f'준비할 슬롯: {[schedule.slot_key(s) for s in slots] or "없음"}')
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a', encoding='utf-8') as f:
            f.write(f'due={"true" if due else "false"}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
