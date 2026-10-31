"""Search Console 일일 기록: 검색 클릭·노출(일별), 상위 검색어·페이지, 최근 글 색인 상태 (2026-10-03 추가).

결과는 data/search_console.json(원자료)과 data/search_console.md(사람이 읽는 요약)에 쓴다. 읽기 전용 권한만 쓴다.
Search Console 숫자는 2~3일 늦게 들어오므로 최근 이틀은 비어 있을 수 있다.
2026-10-31: 90일 검색어×페이지(query_pages)도 저장한다 — sc_longtail이 글감 순서·리라이트 후보에 쓴다.
사용법: python -m src.search_console
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import auto_blogger
from src import history as history_mod, sc_longtail

SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']
SITE = 'sc-domain:woodyway88.com'
DAYS = 28
TOP_N = 20
ALL_PAGES = 1000    # 사이트별 합계용 (블로그·계산기 사이트를 나눠 본다, 2026-10-18)
QUERY_PAGE_DAYS = 90
QUERY_PAGE_LIMIT = 1000
INSPECT_DAYS = 14   # 최근 이 기간에 발행한 글의 색인 상태를 본다 (URL 검사 하루 한도 2,000건 안쪽)
ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = ROOT / 'data' / 'search_console.json'
MD_PATH = ROOT / 'data' / 'search_console.md'
HISTORY_PATH = ROOT / 'data' / 'history.json'


def credentials(env: Mapping[str, str]) -> Credentials:
    config = auto_blogger.load_config(env)
    if config.is_server_mode:
        creds = Credentials(token=None, refresh_token=config.refresh_token, token_uri=auto_blogger.TOKEN_URI,
                            client_id=config.client_id, client_secret=config.client_secret, scopes=SCOPES)
    else:
        creds = Credentials.from_authorized_user_file(config.token_file, SCOPES)
    creds.refresh(Request())
    return creds


def _rows(service: Any, start: date, end: date, dimension: str, limit: int) -> list[dict[str, Any]]:
    body = {'startDate': start.isoformat(), 'endDate': end.isoformat(), 'dimensions': [dimension], 'rowLimit': limit}
    rows = service.searchanalytics().query(siteUrl=SITE, body=body).execute().get('rows', [])
    return [{dimension: r['keys'][0], 'clicks': int(r['clicks']), 'impressions': int(r['impressions']),
             'ctr': round(r['ctr'], 4), 'position': round(r['position'], 1)} for r in rows]


def query_pages(service: Any, start: date, end: date, limit: int = QUERY_PAGE_LIMIT) -> list[dict[str, Any]]:
    """검색어×페이지 줄 (구글이 개인정보 보호로 가린 검색어는 빠진다)."""
    body = {'startDate': start.isoformat(), 'endDate': end.isoformat(), 'dimensions': ['query', 'page'], 'rowLimit': limit}
    rows = service.searchanalytics().query(siteUrl=SITE, body=body).execute().get('rows', [])
    return [{'query': r['keys'][0], 'page': r['keys'][1], 'clicks': int(r['clicks']),
             'impressions': int(r['impressions']), 'position': round(r['position'], 1)} for r in rows]


def by_site(pages: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """페이지별 줄을 호스트(www.woodyway88.com / tools.woodyway88.com)별 클릭·노출 합계로."""
    totals: dict[str, dict[str, int]] = {}
    for row in pages:
        host = row['page'].split('/')[2] if '://' in row['page'] else row['page']
        prev = totals.get(host, {'clicks': 0, 'impressions': 0})
        totals = {**totals, host: {'clicks': prev['clicks'] + row['clicks'],
                                   'impressions': prev['impressions'] + row['impressions']}}
    return totals


def recent_post_urls(history: Sequence[Mapping[str, Any]], today: date, days: int = INSPECT_DAYS) -> list[str]:
    since = (today - timedelta(days=days)).isoformat()
    return [e['url'] for e in history
            if e.get('url') and e.get('status') in ('LIVE', 'SCHEDULED') and e.get('publish_date', '') >= since
            and e['publish_date'] <= today.isoformat()]


def inspect(service: Any, url: str) -> dict[str, str]:
    body = {'inspectionUrl': url, 'siteUrl': SITE}
    result = service.urlInspection().index().inspect(body=body).execute().get('inspectionResult', {})
    status = result.get('indexStatusResult', {})
    return {'url': url, 'verdict': status.get('verdict', ''), 'coverage': status.get('coverageState', ''),
            'last_crawl': status.get('lastCrawlTime', '')}


def collect(service: Any, history: Sequence[Mapping[str, Any]], today: date,
            inspector: Callable[[Any, str], dict[str, str]] = inspect) -> dict[str, Any]:
    start, end = today - timedelta(days=DAYS), today - timedelta(days=1)
    return {
        'updated': today.isoformat(), 'site': SITE, 'range': [start.isoformat(), end.isoformat()],
        'daily': _rows(service, start, end, 'date', DAYS + 1),
        'top_queries': _rows(service, start, end, 'query', TOP_N),
        'top_pages': _rows(service, start, end, 'page', TOP_N),
        'by_site': by_site(_rows(service, start, end, 'page', ALL_PAGES)),
        'index': [inspector(service, url) for url in recent_post_urls(history, today)],
        'query_pages': query_pages(service, today - timedelta(days=QUERY_PAGE_DAYS), end),
    }


def summary(data: Mapping[str, Any], past: Sequence[history_mod.HistoryEntry] = ()) -> str:
    daily = data['daily']
    clicks, imps = sum(d['clicks'] for d in daily), sum(d['impressions'] for d in daily)
    indexed = [i for i in data['index'] if i['verdict'] == 'PASS']
    lines = [f"## Search Console ({data['range'][0]} ~ {data['range'][1]}, 갱신 {data['updated']})",
             f'- 검색 클릭 {clicks}회 · 노출 {imps}회 (최근 {DAYS}일)',
             f"- 최근 {INSPECT_DAYS}일 글 색인: {len(indexed)}/{len(data['index'])}편"]
    sites = sorted(data.get('by_site', {}).items(), key=lambda kv: -kv[1]['clicks'])
    lines += [f"- {host}: 클릭 {t['clicks']} · 노출 {t['impressions']}" for host, t in sites]
    lines += ['', '### 상위 검색어']
    lines += [f"- {q['query']}: 클릭 {q['clicks']} · 노출 {q['impressions']} · 평균 {q['position']}위"
              for q in data['top_queries'][:10]] or ['- (아직 없음)']
    lines += ['', '### 색인 안 된 최근 글']
    lines += [f"- {i['url']} — {i['coverage'] or i['verdict']}" for i in data['index'] if i['verdict'] != 'PASS'] \
        or ['- (없음)']
    lines += ['', *sc_longtail.summary_lines(data.get('query_pages', []), past)]
    return '\n'.join(lines) + '\n'


def main() -> int:
    from dotenv import dotenv_values
    env = {**dotenv_values(ROOT / '.env'), **os.environ}
    service = build('searchconsole', 'v1', credentials=credentials(env), cache_discovery=False)
    history = json.loads(HISTORY_PATH.read_text())
    data = collect(service, history, date.today())
    past = history_mod.load_history(HISTORY_PATH)
    JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n')
    MD_PATH.write_text(summary(data, past))
    print(summary(data, past))
    return 0


if __name__ == '__main__':
    sys.exit(main())
