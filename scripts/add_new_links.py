"""색인된 옛 글에 '새로 나온 관련 글' 상자를 넣는다(2026-11-19).

구글이 11월 새 글을 거의 크롤링하지 않아(발견됨·모름), 이미 색인돼 있어 구글이 다시 찾는 옛 글에서
새 글로 가는 링크를 만든다. 상자는 data-new-links="1" 하나만, 다시 돌리면 내용만 바꾼다.
사용: .venv/bin/python scripts/add_new_links.py [--apply]  (기본은 미리보기만)
원본 백업: data/backup/new_links_before_<날짜>.json (커밋 안 함)
"""
from __future__ import annotations

import html
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOG_ID = '6191091407195774034'
SITE = 'https://www.woodyway88.com'

# 색인된 옛 글 → 새 글(색인 안 됨) 경로. 주제가 같은 것만.
MAP: dict[str, list[str]] = {
    '/2026/10/unemployment-benefit-calculation.html': [
        '/2026/11/unemployment-benefit-2027-daily-limits.html',
        '/2026/11/unemployment-benefit-period-postponement.html',
        '/2026/11/unemployment-benefit-fraud-cases.html'],
    '/2026/10/unemployment-benefit-eligibility.html': [
        '/2026/11/unemployment-benefit-2027-daily-limits.html',
        '/2026/11/unemployment-benefit-fraud-cases.html'],
    '/2026/10/weekly-holiday-pay-calculation.html': [
        '/2026/11/15-hours-per-week-weekly-holiday.html',
        '/2026/11/minimum-wage-2027-take-home-pay.html'],
    '/2026/10/annual-leave-calculation.html': [
        '/2026/11/long-weekends-2027-korea.html',
        '/2026/11/lunar-new-year-holiday-2027.html'],
    '/2026/10/median-income-standard-2027.html': [
        '/2026/11/livelihood-benefit-2027-increase.html',
        '/2026/11/housing-benefit-2027-amount.html',
        '/2026/11/korea-2027-changes-complete-guide.html'],
    '/2026/10/implicit-lease-renewal-termination.html': [
        '/2026/11/lease-registration-order-how-to-apply.html',
        '/2026/11/real-estate-brokerage-fee-rates.html',
        '/2026/11/jeonse-wolse-lease-complete-guide.html'],
    '/2026/10/inheritance-renunciation-deadline-passed.html': [
        '/2026/11/forced-heirship-claim-time-limit.html',
        '/2026/11/gift-tax-exemption-limit-calculation.html'],
}
BOX_RE = re.compile(r'<div data-new-links="1".*?</div>', re.S)


def titles() -> dict[str, str]:
    rows = json.loads((ROOT / 'data' / 'history.json').read_text())
    return {r['url'].replace(SITE, ''): r['title'] for r in rows if r.get('url')}


def box(paths: list[str], names: dict[str, str], blogger) -> str:
    items = []
    for p in paths:
        name = names.get(p) or blogger.posts().getByPath(blogId=BLOG_ID, path=p).execute()['title']
        items.append(f'<li><a href="{SITE}{p}">{html.escape(name)}</a></li>')
    return ('<div data-new-links="1" style="border-left:4px solid #16a34a;background:#f0fdf4;'
            'padding:10px 14px;margin:16px 0">🆕 새로 나온 관련 글<ul style="margin:6px 0 0">'
            + ''.join(items) + '</ul></div>')


def insert(content: str, new_box: str) -> str:
    if BOX_RE.search(content):
        return BOX_RE.sub(new_box, content, count=1)
    hub = re.search(r'<div data-hub-link="1".*?</div>', content, re.S)
    at = hub.end() if hub else content.find('</p>') + 4
    return content[:at] + new_box + content[at:]


def main(argv: list[str]) -> int:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(str(ROOT / 'token.json'))
    creds.refresh(Request())
    blogger = build('blogger', 'v3', credentials=creds)
    names, apply, backup = titles(), '--apply' in argv, {}
    for path, targets in MAP.items():
        post = blogger.posts().getByPath(blogId=BLOG_ID, path=path).execute()
        new = insert(post['content'], box(targets, names, blogger))
        print(path, '→', len(targets), '편', '(변경 없음)' if new == post['content'] else '')
        if apply and new != post['content']:
            backup[path] = {'id': post['id'], 'content': post['content']}
            out = ROOT / 'data' / 'backup' / f'new_links_before_{date.today().isoformat()}.json'
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(backup, ensure_ascii=False, indent=1))   # 고치기 전에 먼저 저장
            blogger.posts().patch(blogId=BLOG_ID, postId=post['id'], body={'content': new}).execute()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
