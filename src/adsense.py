"""애드센스 일일 기록: 사이트별 실제 광고 페이지뷰·수입·클릭 (2026-10-18 추가).

블로거 자체 조회수(월 ~3,700)는 대부분 봇이라 성과 판단에 못 쓴다. 실제 독자는 애드센스 페이지뷰로 본다
(2026-10-18 실측: 10/1~18 페이지뷰 36). 저장소가 공개라 결과는 커밋하지 않는 로컬 파일에만 쓴다
(data/adsense.json·md, .gitignore). 권한은 읽기 전용(adsense.readonly), 토큰은 로컬 token.json.
사용법: .venv/bin/python -m src.adsense
"""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/adsense.readonly']
ACCOUNT = 'accounts/pub-1848068079213715'
DAYS = 28
METRICS = ('PAGE_VIEWS', 'IMPRESSIONS', 'CLICKS', 'ESTIMATED_EARNINGS')
ROOT = Path(__file__).resolve().parent.parent
TOKEN_PATH = ROOT / 'token.json'
JSON_PATH = ROOT / 'data' / 'adsense.json'
MD_PATH = ROOT / 'data' / 'adsense.md'


def credentials(token_path: Path = TOKEN_PATH) -> Credentials:
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    creds.refresh(Request())
    return creds


def _ymd(d: date) -> dict[str, int]:
    return {'year': d.year, 'month': d.month, 'day': d.day}


def parse_report(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """보고서 응답을 줄마다 {date, site, page_views, impressions, clicks, earnings}로."""
    names = [h['name'] for h in report.get('headers', ())]
    rows = []
    for row in report.get('rows', ()):
        cell = dict(zip(names, (c.get('value', '') for c in row.get('cells', ()))))
        rows.append({
            'date': cell.get('DATE', ''), 'site': cell.get('DOMAIN_NAME', ''),
            'page_views': int(cell.get('PAGE_VIEWS') or 0), 'impressions': int(cell.get('IMPRESSIONS') or 0),
            'clicks': int(cell.get('CLICKS') or 0), 'earnings': float(cell.get('ESTIMATED_EARNINGS') or 0),
        })
    return rows


def collect(service: Any, today: date, days: int = DAYS) -> dict[str, Any]:
    start, end = today - timedelta(days=days), today - timedelta(days=1)
    params = {'dateRange': 'CUSTOM', **{f'startDate_{k}': v for k, v in _ymd(start).items()},
              **{f'endDate_{k}': v for k, v in _ymd(end).items()}}
    report = service.accounts().reports().generate(
        account=ACCOUNT, dimensions=['DATE', 'DOMAIN_NAME'], metrics=list(METRICS), **params,
    ).execute()
    return {'updated': today.isoformat(), 'range': [start.isoformat(), end.isoformat()], 'rows': parse_report(report)}


def by_site(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = {}
    for r in rows:
        prev = totals.get(r['site'], {'page_views': 0, 'clicks': 0, 'earnings': 0.0})
        totals = {**totals, r['site']: {'page_views': prev['page_views'] + r['page_views'],
                                        'clicks': prev['clicks'] + r['clicks'],
                                        'earnings': round(prev['earnings'] + r['earnings'], 2)}}
    return totals


def summary(data: Mapping[str, Any]) -> str:
    rows = data['rows']
    week_from = (date.fromisoformat(data['range'][1]) - timedelta(days=6)).isoformat()
    week = [r for r in rows if r['date'] >= week_from]
    lines = [f"## 애드센스 ({data['range'][0]} ~ {data['range'][1]}, 갱신 {data['updated']})",
             f"- 최근 {DAYS}일 페이지뷰 {sum(r['page_views'] for r in rows)} · 클릭 {sum(r['clicks'] for r in rows)}"
             f" · 수입 ${sum(r['earnings'] for r in rows):.2f}",
             f"- 최근 7일 페이지뷰 {sum(r['page_views'] for r in week)}", '', '### 사이트별 (최근 28일)']
    sites = sorted(by_site(rows).items(), key=lambda kv: -kv[1]['page_views'])
    lines += [f"- {site}: 페이지뷰 {t['page_views']:.0f} · 클릭 {t['clicks']:.0f} · ${t['earnings']:.2f}"
              for site, t in sites] or ['- (아직 없음)']
    return '\n'.join(lines) + '\n'


def main() -> int:
    try:
        service = build('adsense', 'v2', credentials=credentials(), cache_discovery=False)
        data = collect(service, date.today())
    except Exception as e:  # noqa: BLE001 - 권한·API 미설정이면 이유만 알리고 끝낸다
        print(f'⚠️ 애드센스 보고서를 못 가져왔습니다: {e}\n'
              'Google Cloud에서 AdSense Management API를 켜고 scripts/authorize_google.py로 다시 로그인하세요.')
        return 1
    JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n')
    MD_PATH.write_text(summary(data))
    print(summary(data))
    return 0


if __name__ == '__main__':
    sys.exit(main())
