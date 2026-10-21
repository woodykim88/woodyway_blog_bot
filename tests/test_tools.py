import json

from src import tools
from src import main as pipeline
from src.renderer import render_post
from src.models import Section
from tests.helpers import make_article, make_draft

TRANSIT = tools.Tool(
    title='모두의카드 환급액 계산기',
    url='https://tools.woodyway88.com/transit-refund-calculator/',
    keywords=('모두의카드', 'K-패스'),
    blurb='이번 달 교통비 환급액을 바로 계산해 보세요.',
)


def test_load_tools_reads_json(tmp_path):
    path = tmp_path / 'tools.json'
    path.write_text(json.dumps([{
        'title': TRANSIT.title, 'url': TRANSIT.url, 'keywords': list(TRANSIT.keywords), 'blurb': TRANSIT.blurb,
    }], ensure_ascii=False), encoding='utf-8')
    assert tools.load_tools(path) == (TRANSIT,)


def test_load_tools_missing_file_is_empty(tmp_path):
    assert tools.load_tools(tmp_path / 'none.json') == ()


def test_load_tools_skips_non_https_urls(tmp_path):
    path = tmp_path / 'tools.json'
    path.write_text(json.dumps([{'title': 'x', 'url': 'javascript:alert(1)', 'keywords': ['x'], 'blurb': ''}]),
                    encoding='utf-8')
    assert tools.load_tools(path) == ()


def test_match_tool_by_keyword_in_title_or_search_keyword():
    draft = make_draft(title='모두의 카드 환급, 오전 9시 타면 최대 83.3%', search_keyword='모두의카드 환급')
    assert tools.match_tool(draft, (TRANSIT,)) == TRANSIT
    assert tools.match_tool(make_draft(), (TRANSIT,)) is None


def test_post_shows_calculator_box_when_tool_matches():
    html = render_post(make_draft(), make_article(), tool=TRANSIT)
    assert TRANSIT.url in html and TRANSIT.title in html
    assert TRANSIT.url not in render_post(make_draft(), make_article())


def test_calculator_button_sits_above_key_points_and_repeats_at_end():
    html = render_post(make_draft(), make_article(), tool=TRANSIT)
    first, last = html.find(TRANSIT.url), html.rfind(TRANSIT.url)
    assert first < html.find('<h2>핵심 내용</h2>')  # 첫 화면에서 바로 보이게
    assert last > html.find('<h2>내일 바로 써먹기</h2>')  # 다 읽은 사람에게 한 번 더
    assert html.count(TRANSIT.url) == 2
    assert 'class="im-btn"' in html[max(0, first - 300):first + 300]  # 글자 링크가 아니라 큰 버튼 (테마 CSS)


def test_success_message_includes_tool_idea():
    composed = pipeline.Composed(make_article(), make_draft(tool_idea='회의록 요약 시간 절약 계산기'))
    msg = pipeline.success_message(composed, {'url': 'https://w/x.html'}, 'now', 'https://edit')
    assert '🧮 계산기 아이디어: 회의록 요약 시간 절약 계산기' in msg
    plain = pipeline.success_message(pipeline.Composed(make_article(), make_draft()), {}, 'now', 'e')
    assert '계산기 아이디어' not in plain


def test_savings_post_with_rate_words_gets_no_mortgage_calculator():
    # 10/19 청년미래적금 글에 '고정금리'·'금리 인상'만으로 주담대 갈아타기 계산기가 붙었다
    body = '청년미래적금은 3년 고정금리로 운영됩니다. 시중 금리 인상기에도 약정 금리가 유지됩니다. ' * 5
    draft = make_draft(
        title='청년미래적금 가입 조건, 소득·나이·일정 한 번에', search_keyword='청년미래적금 가입 조건',
        sections=[Section(heading='금리', body=body, tip='가입 전에 소득 기준을 확인하세요.')] * 3,
    )
    mortgage = tools.find_tool(tools.load_tools(), 'mortgage-refinance-calculator')
    assert tools.match_tool(draft, (mortgage,), tools.MONEY) is None


def test_refinance_post_still_gets_mortgage_calculator_from_context():
    body = '변동금리 대출을 고정금리로 갈아타면 이자 부담이 줄어듭니다. 대출 금리 비교가 먼저입니다. ' * 5
    draft = make_draft(title='대출 이자 줄이는 법', search_keyword='대출 이자 줄이기',
                       sections=[Section(heading='방법', body=body, tip='금리부터 비교하세요.')] * 3)
    mortgage = tools.find_tool(tools.load_tools(), 'mortgage-refinance-calculator')
    assert tools.match_tool(draft, (mortgage,), tools.MONEY) == mortgage
