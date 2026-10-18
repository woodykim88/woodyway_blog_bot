"""2026-10-18: 애드센스 일일 기록 (실제 광고 페이지뷰로 성과를 본다)."""
from datetime import date

from src import adsense

REPORT = {
    'headers': [{'name': n} for n in ('DATE', 'DOMAIN_NAME', *adsense.METRICS)],
    'rows': [
        {'cells': [{'value': v} for v in ('2026-10-16', 'www.woodyway88.com', '3', '5', '0', '0.01')]},
        {'cells': [{'value': v} for v in ('2026-10-17', 'www.woodyway88.com', '2', '1', '1', '0.20')]},
        {'cells': [{'value': v} for v in ('2026-10-17', 'tools.woodyway88.com', '4', '6', '0', '')]},
        {'cells': [{'value': v} for v in ('2026-09-21', 'tools.woodyway88.com', '9', '6', '0', '0')]},
    ],
}


class FakeService:
    def __init__(self):
        self.kwargs = None

    def accounts(self):
        return self

    def reports(self):
        return self

    def generate(self, **kwargs):
        self.kwargs = kwargs
        return self

    def execute(self):
        return REPORT


def test_parse_report_maps_headers_to_fields():
    rows = adsense.parse_report(REPORT)
    assert rows[0] == {'date': '2026-10-16', 'site': 'www.woodyway88.com', 'page_views': 3, 'impressions': 5,
                       'clicks': 0, 'earnings': 0.01}
    assert rows[2]['earnings'] == 0.0  # 빈 값은 0


def test_collect_asks_custom_28_day_range_by_date_and_site():
    service = FakeService()
    data = adsense.collect(service, date(2026, 10, 18))
    assert data['range'] == ['2026-09-20', '2026-10-17']
    assert service.kwargs['account'] == adsense.ACCOUNT
    assert service.kwargs['dimensions'] == ['DATE', 'DOMAIN_NAME']
    assert (service.kwargs['startDate_month'], service.kwargs['startDate_day']) == (9, 20)
    assert service.kwargs['endDate_day'] == 17


def test_summary_totals_week_and_sites():
    data = adsense.collect(FakeService(), date(2026, 10, 18))
    text = adsense.summary(data)
    assert '최근 28일 페이지뷰 18 · 클릭 1 · 수입 $0.21' in text
    assert '최근 7일 페이지뷰 9' in text
    assert text.index('tools.woodyway88.com: 페이지뷰 13') < text.index('www.woodyway88.com: 페이지뷰 5')
