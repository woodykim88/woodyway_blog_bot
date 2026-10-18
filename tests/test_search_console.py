from datetime import date

from src import search_console as sc


class FakeQuery:
    def __init__(self, rows_by_dim):
        self.rows_by_dim = rows_by_dim
        self.body = None

    def query(self, siteUrl, body):
        self.body = body
        self.dim = body['dimensions'][0]
        return self

    def execute(self):
        return {'rows': self.rows_by_dim.get(self.dim, [])}


class FakeService:
    def __init__(self, rows_by_dim):
        self.q = FakeQuery(rows_by_dim)

    def searchanalytics(self):
        return self.q


ROWS = {
    'date': [{'keys': ['2026-10-01'], 'clicks': 1, 'impressions': 40, 'ctr': 0.025, 'position': 12.34}],
    'query': [{'keys': ['교통비 환급'], 'clicks': 1, 'impressions': 30, 'ctr': 0.033, 'position': 8.0}],
}
HISTORY = [
    {'url': 'https://www.woodyway88.com/a.html', 'status': 'LIVE', 'publish_date': '2026-10-01'},
    {'url': 'https://www.woodyway88.com/old.html', 'status': 'LIVE', 'publish_date': '2026-09-01'},
    {'url': 'https://www.woodyway88.com/future.html', 'status': 'SCHEDULED', 'publish_date': '2026-10-05'},
    {'url': '', 'status': 'DRAFT', 'publish_date': '2026-10-02'},
]


def test_recent_post_urls_only_published_within_window():
    assert sc.recent_post_urls(HISTORY, date(2026, 10, 3)) == ['https://www.woodyway88.com/a.html']


def test_collect_and_summary():
    seen = []

    def inspector(_service, url):
        seen.append(url)
        return {'url': url, 'verdict': 'NEUTRAL', 'coverage': 'Discovered - currently not indexed', 'last_crawl': ''}

    data = sc.collect(FakeService(ROWS), HISTORY, date(2026, 10, 3), inspector)
    assert data['range'] == ['2026-09-05', '2026-10-02']
    assert data['daily'][0] == {'date': '2026-10-01', 'clicks': 1, 'impressions': 40, 'ctr': 0.025, 'position': 12.3}
    assert data['top_pages'] == [] and seen == ['https://www.woodyway88.com/a.html']
    text = sc.summary(data)
    assert '검색 클릭 1회 · 노출 40회' in text
    assert '색인: 0/1편' in text
    assert '교통비 환급: 클릭 1' in text
    assert 'a.html — Discovered - currently not indexed' in text


def test_by_site_splits_blog_and_tools_clicks():
    pages = [
        {'page': 'https://tools.woodyway88.com/calendar/2026-11/', 'clicks': 2, 'impressions': 46},
        {'page': 'https://tools.woodyway88.com/calendar/', 'clicks': 0, 'impressions': 8},
        {'page': 'https://www.woodyway88.com/2026/10/a.html', 'clicks': 1, 'impressions': 3},
    ]
    totals = sc.by_site(pages)
    assert totals == {'tools.woodyway88.com': {'clicks': 2, 'impressions': 54},
                      'www.woodyway88.com': {'clicks': 1, 'impressions': 3}}
    data = {'updated': '2026-10-18', 'range': ['a', 'b'], 'daily': [], 'top_queries': [], 'index': [], 'by_site': totals}
    text = sc.summary(data)
    assert text.index('- tools.woodyway88.com: 클릭 2 · 노출 54') < text.index('- www.woodyway88.com: 클릭 1')
