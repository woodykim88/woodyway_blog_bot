"""2026-10-31: Search Console 검색어로 롱테일 글감·리라이트 대상을 고른다."""
import json
from datetime import datetime

from src import howto_topic, schedule, sc_longtail
from tests.helpers import make_entry

KST = schedule.KST
MON = datetime(2026, 10, 19, 12, 0, tzinfo=KST)  # 월요일 = 계산 사례 칸
BLOG = 'https://www.woodyway88.com/2026/10/'
ROWS = [
    {'query': '퇴직금 계산 방법', 'page': BLOG + 'severance-pay-calculation.html', 'clicks': 0, 'impressions': 9, 'position': 60.2},
    {'query': '퇴직금 계산', 'page': BLOG + 'severance-pay-calculation.html', 'clicks': 0, 'impressions': 2, 'position': 63.0},
    {'query': '실업급여 수급 자격', 'page': BLOG + 'unemployment-benefit-eligibility.html', 'clicks': 0, 'impressions': 5, 'position': 6.2},
    {'query': '상속포기 기간 지나면', 'page': BLOG + 'inheritance.html', 'clicks': 0, 'impressions': 1, 'position': 54.0},
    {'query': '11월 마케팅 캘린더', 'page': 'https://tools.woodyway88.com/calendar/2026-11/', 'clicks': 1, 'impressions': 24, 'position': 3.9},
    {'query': '실업급여 계산', 'page': BLOG + 'unemployment-benefit-calculation.html', 'clicks': 1, 'impressions': 3, 'position': 3.0},
]


def guide(query, n, kind=howto_topic.CASE, tool=''):
    sources = (howto_topic.Source(f'기관 {n}', f'https://www.example.go.kr/{n}'),)
    return howto_topic.HowtoGuide(query=query, area='생활경제', slug=f'slug-{n}', sources=sources, tool=tool, kind=kind)


def test_demand_queries_are_blog_queries_not_on_first_page():
    assert sc_longtail.demand_queries(ROWS) == ['퇴직금 계산 방법', '퇴직금 계산', '상속포기 기간 지나면']
    assert sc_longtail.demand_queries(ROWS, {BLOG + 'inheritance.html'}) == ['상속포기 기간 지나면']


def test_matches_demand_by_substring_or_all_words():
    assert sc_longtail.matches_demand('퇴직금 계산 3년 예시', ['퇴직금 계산'])
    assert sc_longtail.matches_demand('3년 근무 퇴직금 계산', ['퇴직금 계산'])  # 어순이 달라도 단어가 다 있으면
    assert not sc_longtail.matches_demand('10년 퇴직금 얼마', ['퇴직금 계산'])
    assert not sc_longtail.matches_demand('연차 수당', ['연'])  # 너무 짧은 검색어는 무시


def test_is_generic_flags_broad_queries_only():
    for broad in ('근로장려금 신청 방법', '퇴직금 계산 방법', '전입신고 방법', '기초연금 수급 자격', '현금영수증 소득공제'):
        assert sc_longtail.is_generic(broad), broad
    for narrow in ('퇴직금 1년 미만', '실업급여 자진퇴사 조건', '연봉 4000 퇴직금', '상속포기 기간 지나면', '2027 설날 연휴'):
        assert not sc_longtail.is_generic(narrow), narrow


def test_rewrite_targets_put_calculator_posts_first_and_skip_tools_site():
    past = (make_entry(url=BLOG + 'severance-pay-calculation.html', tool='severance-pay-calculator'),
            make_entry(url=BLOG + 'unemployment-benefit-eligibility.html', tool='unemployment-benefit-calculator'),
            make_entry(url=BLOG + 'inheritance.html'))
    targets = sc_longtail.rewrite_targets(ROWS, past)
    assert sc_longtail.rewrite_targets(ROWS, past[:1])[0]['page'].endswith('severance-pay-calculation.html')
    assert len(sc_longtail.rewrite_targets(ROWS, past[:1])) == 1  # 발행 기록에 없는 글(초안으로 내린 글 등)은 뺀다
    assert [t['page'].rsplit('/', 1)[-1] for t in targets] == [
        'severance-pay-calculation.html', 'unemployment-benefit-eligibility.html', 'inheritance.html']
    first = targets[0]
    assert first['impressions'] == 11 and first['clicks'] == 0 and first['top_query'] == '퇴직금 계산 방법'
    assert first['tool'] == 'severance-pay-calculator'


def test_load_reads_query_pages_and_tolerates_missing_file(tmp_path):
    path = tmp_path / 'sc.json'
    assert sc_longtail.load(path) == []
    path.write_text(json.dumps({'query_pages': ROWS}), encoding='utf-8')
    assert sc_longtail.load(path) == ROWS


def test_summary_lines_list_rewrite_targets():
    lines = sc_longtail.summary_lines(ROWS, (make_entry(url=BLOG + 'severance-pay-calculation.html'),))
    assert lines[0].startswith('### 리라이트 후보')
    assert any('severance-pay-calculation.html' in line and '퇴직금 계산 방법' in line for line in lines)


# --- 글감 순서에 반영 ---

def test_order_prefers_demand_matched_guide_within_kind():
    plain = guide('10년 퇴직금 얼마', 1)
    demanded = guide('3년 근무 퇴직금 계산', 2)
    assert howto_topic.order_guides([plain, demanded], (), MON)[0] == plain  # 수요 없으면 목록 순서
    assert howto_topic.order_guides([plain, demanded], (), MON, demand=['퇴직금 계산'])[0] == demanded


def test_order_puts_generic_queries_after_specific_ones_within_kind():
    broad = guide('근로장려금 신청 방법', 1, kind=howto_topic.GUIDE)
    narrow = guide('실업급여 자진퇴사 조건', 2, kind=howto_topic.GUIDE)
    order = howto_topic.order_guides([broad, narrow], (), MON, want=howto_topic.GUIDE)
    assert order == [narrow, broad]
