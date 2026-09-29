import json

from src import tools
from src import main as pipeline
from src.renderer import render_post
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


def test_success_message_includes_tool_idea():
    composed = pipeline.Composed(make_article(), make_draft(tool_idea='회의록 요약 시간 절약 계산기'))
    msg = pipeline.success_message(composed, {'url': 'https://w/x.html'}, 'now', 'https://edit')
    assert '🧮 계산기 아이디어: 회의록 요약 시간 절약 계산기' in msg
    plain = pipeline.success_message(pipeline.Composed(make_article(), make_draft()), {}, 'now', 'e')
    assert '계산기 아이디어' not in plain
