"""2026-10-18: 주간 성적표 (월요일 아침 텔레그램)."""
from datetime import date, timedelta

from src import weekly_report as wr

TODAY = date(2026, 10, 26)


def days(start: date, n: int, **fields):
    return [{'date': (start + timedelta(days=i)).isoformat(), **fields} for i in range(n)]


SC = {
    'daily': days(date(2026, 10, 10), 14, clicks=1, impressions=10)[:7]
    + days(date(2026, 10, 17), 7, clicks=2, impressions=20),
    'by_site': {'tools.woodyway88.com': {'clicks': 15, 'impressions': 150}, 'www.woodyway88.com': {'clicks': 6, 'impressions': 60}},
    'top_queries': [{'query': '2027 공휴일', 'clicks': 3, 'impressions': 40, 'position': 4.2},
                    {'query': '드문 검색어', 'clicks': 0, 'impressions': 1, 'position': 50}],
    'index': [{'verdict': 'PASS'}, {'verdict': 'NEUTRAL'}],
}
ADS = {'rows': [{**r, 'site': 'www.woodyway88.com'} for r in days(date(2026, 10, 12), 14, page_views=3, earnings=0.01)]
       + [{**r, 'site': 'tools.woodyway88.com'} for r in days(date(2026, 10, 19), 7, page_views=5, earnings=0.0)]}
HISTORY = [
    {'publish_date': '2026-10-19', 'title': '3년 근무 퇴직금, 약 881만원', 'url': 'u1', 'labels': ['생활가이드', '생활경제', '계산사례']},
    {'publish_date': '2026-10-25', 'title': '2027 공휴일', 'url': 'u2', 'labels': ['생활가이드', '일정']},
    {'publish_date': '2026-10-26', 'title': '오늘 글은 다음 주에', 'url': 'u3', 'labels': []},
    {'publish_date': '2026-10-20', 'title': '초안', 'url': '', 'labels': []},
]


def test_week_over_week_change():
    w = wr.week_windows(SC['daily'], 'clicks', date(2026, 10, 23))
    assert (w.now, w.before, w.change) == (14, 7, '+100%')
    assert wr.Window(3, 0).change == '새로 생김' and wr.Window(0, 0).change == '변화 없음'


def test_next_goal_and_posts():
    assert wr.next_goal(TODAY) == (date(2026, 11, 15), 30)
    assert wr.next_goal(date(2026, 11, 16)) == (date(2026, 12, 31), 150)
    assert wr.next_goal(date(2027, 3, 1)) is None
    assert [e['title'] for e in wr.posts_last_week(HISTORY, TODAY)] == ['3년 근무 퇴직금, 약 881만원', '2027 공휴일']


def test_report_has_search_visits_posts_and_goal():
    text = wr.build_report(SC, ADS, HISTORY, TODAY)
    assert '클릭 14회 (+100%)' in text
    assert '28일 계산기: 클릭 15' in text and '28일 블로그: 클릭 6' in text
    assert '2027 공휴일(4.2위)' in text and '드문 검색어' not in text
    assert '페이지뷰 56 (+167%) · 블로그 21 · 계산기 35' in text
    assert '[계산사례] 3년 근무 퇴직금' in text and '[일정] 2027 공휴일' in text
    assert '지난주 발행 2편 · 최근 14일 글 색인 1/2' in text
    assert '11/15까지 28일 검색 클릭 30회' in text and '지금 21회 (70%)' in text


def test_report_without_adsense_still_sends():
    assert '애드센스 리포트가 없어 건너뜀' in wr.build_report(SC, None, [], TODAY)
