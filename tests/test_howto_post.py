"""2026-10-03: 20:00 칸 검색형 생활 가이드 글 (글감 고르기, 칸 배정, 원고 입력, 계산기, 렌더링)."""
import json
import re
from datetime import datetime

import pytest

from src import history, howto_topic, providers, schedule, thumbnail, tools, topics, writer
from src import main as pipeline
from src.llm import LLMError
from src.models import Article, FactCheck, Section
from src.renderer import render_post
from tests.helpers import NOW, fake_llm, make_article, make_draft, make_entry

KST = schedule.KST
MODELS = pipeline.Models(writer='writer-model', fast='fast-model')
OK = FactCheck(claims=[])
ALIVE = lambda url: True  # noqa: E731
OCT = datetime(2026, 10, 3, 12, 0, tzinfo=KST)
PAGE = '<html><body><article>' + ''.join(
    f'<p>근로장려금은 홈택스에서 신청합니다. 신청 기간과 자격 요건을 확인하세요 {i}번째 안내 문단입니다.</p>' for i in range(30)
) + '</article></body></html>'


def guide(query, n, months=(), tool='', area='지원금·혜택'):
    sources = tuple(howto_topic.Source(f'기관 {n}-{i}', f'https://www.example.go.kr/{n}/{i}') for i in range(2))
    return howto_topic.HowtoGuide(query=query, area=area, slug=f'slug-{n}', sources=sources,
                                  months=tuple(months), tool=tool)


def howto_draft(**overrides):
    fields = {
        'title': '근로장려금 신청 방법 총정리: 자격·기간 한 번에',
        'search_keyword': '아무 검색어',  # 코드가 목표 검색어로 바꾼다
        'meta_description': '근로장려금 신청 방법: 홈택스에서 신청 기간 안에 신청합니다.',
        'labels': ['생활경제'],
        'url_slug': 'llm-slug',
    }
    return make_draft(**{**fields, **overrides})


# --- 글감 고르기 ---

def test_order_prefers_in_season_unused_then_least_recently_used_then_off_season():
    evergreen = guide('실업급여 신청 방법', 1)
    october = guide('청년미래적금 가입 조건', 2, months=(10,))
    may_only = guide('종합소득세 신고 방법', 3, months=(5,))
    used_old = guide('퇴직금 계산 방법', 4)
    used_new = guide('전입신고 방법', 5)
    past = (make_entry(source_url=used_old.urls[1]), make_entry(source_url=used_new.urls[0]))
    order = howto_topic.order_guides([may_only, used_new, evergreen, used_old, october], past, OCT)
    assert order == [evergreen, october, used_old, used_new, may_only]


def test_off_season_topic_is_picked_in_its_month():
    may_only = guide('종합소득세 신고 방법', 3, months=(5,))
    evergreen = guide('실업급여 신청 방법', 1)
    may = datetime(2026, 5, 10, tzinfo=KST)
    assert howto_topic.order_guides([evergreen, may_only], (), may)[0] == evergreen  # 둘 다 제철이면 목록 순서
    assert howto_topic.order_guides([may_only, evergreen], (), OCT) == [evergreen, may_only]


def test_guide_list_is_curated_official_sources():
    guides = howto_topic.load_guides()
    assert len(guides) >= 25
    assert len({g.query for g in guides}) == len(guides)
    tool_ids = {tools.tool_id(t) for t in tools.load_tools()}
    for g in guides:
        assert 1 <= len(g.sources) <= 3
        assert all(re.match(r'https://[^/]*(go\.kr|korea\.kr|or\.kr)/', s.url) for s in g.sources), g.query
        assert re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+){2,7}', g.slug), g.slug
        assert all(1 <= m <= 12 for m in g.months)
        assert g.area in topics.SPECS[topics.HOWTO].labels
        assert not g.tool or g.tool in tool_ids


def test_load_guides_skips_rows_without_query_or_https_source(tmp_path):
    path = tmp_path / 'g.json'
    path.write_text(json.dumps([
        {'query': '', 'sources': [{'name': 'a', 'url': 'https://x.go.kr/a'}]},
        {'query': '전입신고 방법', 'sources': [{'name': 'a', 'url': 'http://x.go.kr/a'}]},
        {'query': '퇴직금 계산 방법', 'slug': 'severance-pay-calculation',
         'sources': [{'name': '법제처', 'url': 'https://x.go.kr/b'}], 'months': [13, 5]},
    ]), encoding='utf-8')
    [only] = howto_topic.load_guides(path)
    assert only.query == '퇴직금 계산 방법' and only.months == (5,) and only.area == '생활경제'


def test_build_article_skips_unreadable_source_and_keeps_rest_as_references():
    g = guide('근로장려금 신청 방법', 1)
    g = howto_topic.HowtoGuide(g.query, g.area, g.slug, (howto_topic.Source('죽은 페이지', 'https://dead.go.kr/x'), *g.sources))

    def fetch(candidate):
        if 'dead' in candidate.link:
            return None
        return Article(candidate=candidate, source_url=candidate.link, text=f'{candidate.link} 본문 ' * 50)

    article = howto_topic.build_article(g, fetch, OCT)
    assert article.source_url == g.sources[1].url
    assert [r.url for r in article.references] == [g.sources[2].url]
    assert '공식 안내 1' in article.text and '공식 안내 2' in article.text
    assert howto_topic.build_article(g, lambda c: None, OCT) is None


# --- 원고 입력 ---

def test_writer_input_carries_target_query_and_official_sources():
    article = make_article()
    text = writer.build_writer_input(article, '생활 가이드', target_query='근로장려금 신청 방법')
    assert '목표 검색어: 근로장려금 신청 방법' in text
    assert article.source_url in text
    assert '관련 기사' not in text


def test_howto_instructions_add_howto_rules_after_common_rules():
    text = writer.instructions(topics.HOWTO)
    assert text.index('생활 가이드 글 규칙') > text.index('[제목(title) 규칙]')
    assert '자주 묻는 질문' in text and 'Q. ' in text and '루이' not in text


def test_compose_howto_post_end_to_end(monkeypatch):
    g = guide('근로장려금 신청 방법', 1, tool='mortgage-refinance-calculator')
    monkeypatch.setattr(howto_topic, 'load_guides', lambda: (g,))
    llm, client = fake_llm({'PostDraft': [howto_draft()], 'FactCheck': [OK]})
    composed = pipeline.compose_howto_post(llm, MODELS, OCT, (), get=lambda url: PAGE, is_alive=ALIVE)
    assert composed.category == topics.HOWTO
    assert composed.draft.labels == [topics.HOWTO_LABEL, '지원금·혜택']
    assert composed.draft.search_keyword == '근로장려금 신청 방법'
    assert composed.draft.url_slug == 'slug-1'
    assert composed.tool is not None and 'mortgage' in composed.tool.url
    assert composed.as_of == '2026년 10월 3일'
    request = client.requests[0]
    assert '목표 검색어: 근로장려금 신청 방법' in request['input']
    assert all(url in request['input'] for url in g.urls)
    assert '생활 가이드 글 규칙' in request['instructions']
    assert thumbnail.mascot_for(topics.HOWTO) is None  # 루이 캐릭터는 쓰지 않는다


def test_compose_howto_post_title_without_query_fails_seo_check(monkeypatch):
    monkeypatch.setattr(howto_topic, 'load_guides', lambda: (guide('근로장려금 신청 방법', 1),))
    bad = howto_draft(title='일하는데 소득이 적다면 받을 수 있는 돈')
    llm, _ = fake_llm({'PostDraft': [bad, bad], 'FactCheck': []})
    assert pipeline.compose_howto_post(llm, MODELS, OCT, (), get=lambda url: PAGE, is_alive=ALIVE, limit=1) is None


def test_compose_howto_post_tries_at_most_limit_topics(monkeypatch):
    monkeypatch.setattr(howto_topic, 'load_guides', lambda: tuple(guide(f'주제 {i} 신청 방법', i) for i in range(4)))
    seen = []
    monkeypatch.setattr(pipeline, 'draft_article', lambda *a, target_query='', **k: seen.append(target_query))
    assert pipeline.compose_howto_post(*fake_llm({})[:1], MODELS, OCT, (), get=lambda url: PAGE) is None
    assert seen == ['주제 0 신청 방법', '주제 1 신청 방법']


# --- 계산기 ---

def test_find_tool_by_id_and_howto_tool_falls_back_to_content_match():
    available = tools.load_tools()
    assert 'transit' in tools.find_tool(available, 'transit-refund-calculator').url
    assert tools.find_tool(available, '') is None and tools.find_tool(available, 'nope') is None
    draft = howto_draft(meta_description='주담대 갈아타기 조건과 신청 방법')
    assert 'mortgage' in pipeline.howto_tool(guide('주택담보대출 갈아타기 조건', 1), draft).url
    assert pipeline.howto_tool(guide('전입신고 방법', 2), make_draft(labels=['생활경제'])) is None


# --- 20:00 칸 배정과 예비 ---

def test_evening_slot_uses_howto_post(monkeypatch):
    monkeypatch.setattr(pipeline, 'compose_howto_post', lambda *a, **k: 'howto')
    monkeypatch.setattr(pipeline, 'compose_post', lambda *a, **k: pytest.fail('news'))
    assert pipeline.compose_evening_post(*fake_llm({})[:1], MODELS, NOW, ()) == 'howto'


@pytest.mark.parametrize('howto', [lambda *a, **k: None, lambda *a, **k: (_ for _ in ()).throw(LLMError('x'))])
def test_evening_slot_falls_back_to_news_when_howto_fails(monkeypatch, howto):
    monkeypatch.setattr(pipeline, 'compose_howto_post', howto)
    seen = {}

    def news(*a, only=None, **k):
        seen['only'] = only
        return 'news'

    monkeypatch.setattr(pipeline, 'compose_post', news)
    assert pipeline.compose_evening_post(*fake_llm({})[:1], MODELS, NOW, ()) == 'news'
    assert seen['only'] is None  # 평소 뉴스 비율대로


@pytest.fixture
def routed(monkeypatch, tmp_path):
    monkeypatch.setattr(history, 'DEFAULT_HISTORY_PATH', tmp_path / 'history.json')
    monkeypatch.setattr(pipeline.providers, 'build_text_backend', lambda p, env: None)
    monkeypatch.setattr(pipeline.providers, 'build_image_backend', lambda p, env: None)
    calls = []
    composed = pipeline.Composed(make_article(), make_draft())

    def fake(name):
        return lambda *a, **k: calls.append(name) or composed

    monkeypatch.setattr(pipeline, 'compose_evening_post', fake('howto'))
    monkeypatch.setattr(pipeline, 'compose_slot_post', fake('skill'))
    monkeypatch.setattr(pipeline, 'compose_post', fake('news'))
    monkeypatch.setattr(pipeline, 'attach_hero_image', lambda c, *a, **k: c)
    monkeypatch.setattr(pipeline, 'write_preview', lambda c: 'preview.html')
    return calls


@pytest.mark.parametrize('at, howto_time, expected', [
    ((20, 0), '', 'howto'), ((8, 0), '', 'howto'), ((12, 30), '', 'skill'),  # 2026-10-10: 08:00도 생활 가이드
    ((8, 0), '20:00', 'news'), ((20, 0), '20:00', 'howto'),                  # HOWTO_TIME=20:00이면 08:00은 다시 뉴스
])
def test_run_routes_slot_by_time(routed, monkeypatch, at, howto_time, expected):
    target = pipeline.Target('slot', datetime(2026, 10, 4, *at, tzinfo=KST), 'preview')
    monkeypatch.setattr(pipeline, 'plan_targets', lambda *a, **k: [target])
    env = {'PUBLISH_TIMES': '08:00,12:30,20:00', 'HOWTO_TIME': howto_time}
    result = pipeline.run('preview', None, env, providers.load_ai_settings({}), None, now=NOW)
    assert result.outcome == 'success' and routed == [expected]


def test_run_manual_post_is_news(routed):
    pipeline.run('preview', None, {}, providers.load_ai_settings({}), None, now=NOW)
    assert routed == ['news']


# --- 분야 비율·라벨 ---

def test_howto_posts_do_not_count_toward_news_balance():
    assert topics.category_of(make_entry(labels=('생활가이드', '생활경제'))) == topics.HOWTO
    money_heavy = (make_entry(labels=('생활가이드', '재테크')),) * 9
    assert topics.choose_category(money_heavy) == topics.TECH  # 뉴스가 없으면 AI·IT부터
    past = (make_entry(labels=('AI활용',)), make_entry(labels=('AI활용',)), *money_heavy)
    assert topics.choose_category(past) == topics.MONEY


def test_news_draft_never_keeps_howto_label():
    draft = pipeline.drop_howto_label(make_draft(labels=['생활가이드']), '재테크')
    assert draft.labels == ['재테크']
    assert pipeline.drop_howto_label(make_draft(labels=['생활가이드', 'AI활용']), 'x').labels == ['AI활용']


# --- 렌더링 ---

def test_render_howto_steps_faq_as_of_and_official_sources():
    steps = '1. 홈택스에 접속합니다.\n2. 장려금 메뉴를 고릅니다.\n3. 신청서를 제출합니다.'
    faq = 'Q. 언제 받나요?\n\nA. 9월 말까지 지급됩니다.'
    sections = [Section(heading='신청 방법', body=f'먼저 확인하세요.\n\n{steps}', tip='팁'),
                Section(heading='근로장려금 자주 묻는 질문', body=faq, tip='팁')]
    candidate = howto_topic.to_candidate(guide('근로장려금 신청 방법', 1), howto_topic.Source('국세청 안내', 'https://www.nts.go.kr/a'), OCT)
    article = Article(candidate=candidate, source_url='https://www.nts.go.kr/a', text='본문')
    html = render_post(make_draft(sections=sections), article, as_of='2026년 10월 3일')
    assert '<ol>\n  <li>홈택스에 접속합니다.</li>' in html
    assert '<h3>언제 받나요?</h3>' in html and '<p>9월 말까지 지급됩니다.</p>' in html
    assert '2026년 10월 3일 기준 공식 안내' in html
    assert '공식 안내: 국세청 안내' in html


def test_render_news_post_unchanged_by_howto_features():
    html = render_post(make_draft(), make_article())
    assert '기준 공식 안내를 바탕으로' not in html
    assert '원문: ' in html and '<ol>' not in html.split('내일 바로 써먹기')[0]
    assert html.count('<h3>') == 2  # 용어사전·출처만


def test_guides_sharing_an_official_page_are_tracked_by_post_slug():
    """2026-10-10 롱테일 글감: 같은 공식 페이지를 쓰는 두 글감 중 하나를 써도 다른 하나는 안 쓴 글감으로 남는다."""
    shared = howto_topic.Source('찾기쉬운 생활법령정보 「임대차계약의 갱신」', 'https://easylaw.go.kr/x')
    first = howto_topic.HowtoGuide('묵시적 갱신 해지 3개월', '부동산', 'implicit-renewal-termination', (shared,))
    second = howto_topic.HowtoGuide('계약갱신청구권 행사 방법', '부동산', 'renewal-right-how-to-use', (shared,))
    past = (make_entry(source_url=shared.url, url='https://www.woodyway88.com/2026/10/implicit-renewal-termination.html'),)
    assert howto_topic.order_guides([first, second], past, OCT) == [second, first]


def test_long_tail_guides_come_first():
    guides = howto_topic.load_guides()
    assert guides[0].query == '실업급여 자진퇴사 조건' and len(guides) >= 50
    assert howto_topic.order_guides(guides, (), OCT)[0] == guides[0]
