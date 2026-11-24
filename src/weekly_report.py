"""주간 성적표: 매주 월요일 아침 지난주 블로그·계산기 사이트 성과를 보낸다 (2026-10-18 추가).
2026-11-25부터 텔레그램 직접 전송 대신 텔레그램 알림함(ok)에 남겨 08:30 아침 묶음 표로 간다.

재료는 이미 매일 쌓이는 파일만 쓴다: data/search_console.json(검색, 2~3일 늦음), data/adsense.json(실제 방문·수익,
로컬 전용), data/history.json(발행 글). 목표는 2026-10-18 방향 전환 때 정한 28일 검색 클릭(블로그+계산기 합계).
사용법: .venv/bin/python -m src.weekly_report [--dry-run]  (scripts/weekly_report.sh가 데이터를 갱신한 뒤 부른다)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
WEEK = 7
# 28일 검색 클릭 목표 (2026-10-18 사용자 승인 계획). 마지막 목표는 월 클릭이라 28일 합계로 근사
GOALS: tuple[tuple[date, int], ...] = ((date(2026, 11, 15), 30), (date(2026, 12, 31), 150), (date(2027, 2, 28), 1000))
KIND_LABELS = ('계산사례', '일정', '바뀌는제도')
# 클라우드(main.py)는 src/notifier로 텔레그램에 직접 보내고, 로컬에서만 도는 주간 성적표는 알림함으로 보낸다
NOTIFY = Path.home() / 'Projects/도구/텔레그램-알림함/notify'
INBOX_PROJECT = '정보마스터 주간'
NOTIFY_TIMEOUT = 60


@dataclass(frozen=True)
class Window:
    now: float
    before: float

    @property
    def change(self) -> str:
        if self.before == 0:
            return '새로 생김' if self.now else '변화 없음'
        pct = (self.now - self.before) / self.before * 100
        return f'{pct:+.0f}%'


def _sum(rows: Sequence[Mapping[str, Any]], key: str, start: date, end: date, field: str = 'date') -> float:
    """start 이상 end 미만인 날짜의 key 합계."""
    return sum(r.get(key, 0) for r in rows if start.isoformat() <= r[field] < end.isoformat())


def week_windows(rows: Sequence[Mapping[str, Any]], key: str, last_day: date) -> Window:
    """last_day까지 7일 vs 그 앞 7일."""
    end = last_day + timedelta(days=1)
    return Window(_sum(rows, key, end - timedelta(days=WEEK), end),
                  _sum(rows, key, end - timedelta(days=2 * WEEK), end - timedelta(days=WEEK)))


def last_data_day(rows: Sequence[Mapping[str, Any]], fallback: date) -> date:
    return max((date.fromisoformat(r['date']) for r in rows), default=fallback)


def next_goal(today: date, goals: Sequence[tuple[date, int]] = GOALS) -> tuple[date, int] | None:
    return next(((d, n) for d, n in goals if d >= today), None)


def posts_last_week(history: Sequence[Mapping[str, Any]], today: date) -> list[Mapping[str, Any]]:
    start = (today - timedelta(days=WEEK)).isoformat()
    return [e for e in history if start <= e.get('publish_date', '') < today.isoformat() and e.get('url')]


def kind_of(entry: Mapping[str, Any]) -> str:
    return next((k for k in KIND_LABELS if k in entry.get('labels', ())), '안내')


def build_report(sc: Mapping[str, Any], ads: Mapping[str, Any] | None, history: Sequence[Mapping[str, Any]],
                 today: date) -> str:
    daily = sc.get('daily', [])
    sc_day = last_data_day(daily, today - timedelta(days=3))
    clicks = week_windows(daily, 'clicks', sc_day)
    imps = week_windows(daily, 'impressions', sc_day)
    clicks28 = int(sum(d['clicks'] for d in daily))
    lines = [f'📊 구글 블로그 주간 성적표 ({today.month}/{today.day})', '',
             f'🔎 검색 (~{sc_day.month}/{sc_day.day}, 7일)',
             f'- 클릭 {clicks.now:.0f}회 ({clicks.change}) · 노출 {imps.now:.0f}회 ({imps.change})']
    sites = sorted(sc.get('by_site', {}).items(), key=lambda kv: -kv[1]['clicks'])
    lines += [f"- 28일 {host.split('.')[0].replace('www', '블로그').replace('tools', '계산기')}: 클릭 {t['clicks']} · 노출 {t['impressions']}"
              for host, t in sites]
    top = [q for q in sc.get('top_queries', []) if q['clicks'] or q['impressions'] >= 5][:3]
    if top:
        lines.append('- 많이 뜬 검색어: ' + ', '.join(f"{q['query']}({q['position']}위)" for q in top))

    if ads and ads.get('rows'):
        rows = ads['rows']
        ad_day = last_data_day(rows, today - timedelta(days=1))
        pv = week_windows(rows, 'page_views', ad_day)
        earn = week_windows(rows, 'earnings', ad_day)
        blog = week_windows([r for r in rows if r['site'].startswith('www.')], 'page_views', ad_day)
        tools = week_windows([r for r in rows if r['site'].startswith('tools.')], 'page_views', ad_day)
        lines += ['', '👀 실제 방문 (애드센스, 7일)',
                  f'- 페이지뷰 {pv.now:.0f} ({pv.change}) · 블로그 {blog.now:.0f} · 계산기 {tools.now:.0f}',
                  f'- 수익 ${earn.now:.2f} ({earn.change})']
    else:
        lines += ['', '👀 실제 방문: 애드센스 리포트가 없어 건너뜀']

    posts = posts_last_week(history, today)
    index = sc.get('index', [])
    indexed = sum(1 for i in index if i.get('verdict') == 'PASS')
    lines += ['', f'📝 지난주 발행 {len(posts)}편 · 최근 14일 글 색인 {indexed}/{len(index)}']
    lines += [f"- [{kind_of(e)}] {e['title']}" for e in posts]

    goal = next_goal(today)
    if goal:
        when, target = goal
        pct = min(100, clicks28 * 100 // target)
        lines += ['', f'🎯 목표: {when.month}/{when.day}까지 28일 검색 클릭 {target}회',
                  f'- 지금 {clicks28}회 ({pct}%) · {(when - today).days}일 남음']
    return '\n'.join(lines)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def send_to_inbox(text: str, run: Any = subprocess.run) -> bool:
    """알림함에 ok로 남긴다. 실패해도 예외 대신 로그와 False (성적표 본문은 이미 로그에 찍혔다)."""
    cmd = [str(NOTIFY), '--status', 'ok', '--project', INBOX_PROJECT, '--text', text]
    try:
        done = run(cmd, capture_output=True, text=True, timeout=NOTIFY_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f'[알림함 실패] {type(e).__name__}: {e}')
        return False
    if done.returncode != 0:
        print(f'[알림함 실패] 종료 코드 {done.returncode}: {(done.stderr or "").strip()[:300]}')
        return False
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='보내지 않고 화면에만 출력')
    args = parser.parse_args(argv)
    from dotenv import dotenv_values
    env = {**dotenv_values(ROOT / '.env'), **os.environ}
    sc = _load(DATA / 'search_console.json') or {}
    text = build_report(sc, _load(DATA / 'adsense.json'), _load(DATA / 'history.json') or [], date.today())
    print(text)
    if args.dry_run:
        return 0
    return 0 if send_to_inbox(text) else 1


if __name__ == '__main__':
    sys.exit(main())
