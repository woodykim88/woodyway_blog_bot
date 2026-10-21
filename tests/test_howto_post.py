"""2026-10-03: 20:00 칸 검색형 생활 가이드 글 (글감 고르기, 칸 배정, 원고 입력, 계산기, 렌더링)."""
import json
from pathlib import Path
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


def guide(query, n, months=(), tool='', area='지원금·혜택', kind=howto_topic.GUIDE):
    sources = tuple(howto_topic.Source(f'기관 {n}-{i}', f'https://www.example.go.kr/{n}/{i}') for i in range(2))
    return howto_topic.HowtoGuide(query=query, area=area, slug=f'slug-{n}', sources=sources,
                                  months=tuple(months), tool=tool, kind=kind)


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


# --- 2026-10-18 글 종류 요일 순환 (계산 사례 3 · 일정 2 · 숫자 변경 2) ---

MON = datetime(2026, 10, 19, 20, 0, tzinfo=KST)  # 계산 사례
THU = datetime(2026, 10, 22, 20, 0, tzinfo=KST)  # 숫자 변경


def test_kind_of_day_rotates_case_schedule_change():
    days = [howto_topic.kind_of_day(datetime(2026, 10, 19 + i, 20, 0, tzinfo=KST)) for i in range(7)]
    assert days.count(howto_topic.CASE) == 3 and days.count(howto_topic.SCHEDULE) == 2
    assert days.count(howto_topic.CHANGE) == 2
    assert days[0] == howto_topic.CASE and days[3] == howto_topic.CHANGE


def test_order_puts_todays_kind_first_then_new_kinds_then_old_guides():
    old = guide('실업급여 신청 방법', 1)
    change = guide('2027 최저임금', 2, kind=howto_topic.CHANGE)
    schedule_ = guide('2027 설 연휴', 3, kind=howto_topic.SCHEDULE)
    case = guide('퇴직금 3년 월급 300', 4, kind=howto_topic.CASE)
    assert howto_topic.order_guides([old, change, schedule_, case], (), MON) == [case, change, schedule_, old]
    assert howto_topic.order_guides([old, change, schedule_, case], (), THU)[0] == change


def test_morning_slot_kind_is_old_guide_and_evening_follows_weekday():
    assert howto_topic.kind_for_slot(datetime(2026, 10, 19, 8, 0, tzinfo=KST)) == howto_topic.GUIDE
    assert howto_topic.kind_for_slot(MON) == howto_topic.CASE
    assert howto_topic.kind_for_slot(THU) == howto_topic.CHANGE
    assert howto_topic.kind_for_slot(None) == ''  # 수동 발행은 요일 규칙 그대로


def test_order_with_old_guide_kind_puts_old_guides_first_then_new_kinds():
    old = guide('실업급여 신청 방법', 1)
    change = guide('2027 최저임금', 2, kind=howto_topic.CHANGE)
    case = guide('퇴직금 3년 월급 300', 4, kind=howto_topic.CASE)
    order = howto_topic.order_guides([change, case, old], (), MON, want=howto_topic.GUIDE)
    assert order == [old, change, case]
    used_old = (make_entry(url='https://www.woodyway88.com/2026/10/slug-1.html'),)
    assert howto_topic.order_guides([old, case], used_old, MON, want=howto_topic.GUIDE) == [case, old]


def test_unused_other_kind_beats_reusing_todays_kind():
    case = guide('퇴직금 3년 월급 300', 4, kind=howto_topic.CASE)
    change = guide('2027 최저임금', 2, kind=howto_topic.CHANGE)
    past = (make_entry(url='https://www.woodyway88.com/2026/10/slug-4.html'),)
    assert howto_topic.order_guides([case, change], past, MON) == [change, case]


def test_kind_is_parsed_and_unknown_kind_falls_back_to_guide(tmp_path):
    rows = [{'query': q, 'slug': s, 'kind': k, 'sources': [{'name': '기관', 'url': 'https://www.nts.go.kr/x'}]}
            for q, s, k in (('가', 'a', 'case'), ('나', 'b', 'weird'), ('다', 'c', None))]
    path = tmp_path / 'g.json'
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    assert [g.kind for g in howto_topic.load_guides(path)] == ['case', 'guide', 'guide']


@pytest.mark.parametrize('kind', [howto_topic.CASE, howto_topic.SCHEDULE, howto_topic.CHANGE])
def test_each_new_kind_has_writer_rules_that_reach_the_writer_input(kind):
    rules = pipeline.howto_kind_rules(kind)
    assert len(rules) > 200
    text = writer.build_writer_input(make_article(), '생활 가이드', target_query='검색어', kind_rules=rules)
    assert rules.strip() in text
    assert pipeline.howto_kind_rules(howto_topic.GUIDE) == ''


def test_compose_howto_post_sends_kind_rules_to_writer(monkeypatch):
    monkeypatch.setattr(howto_topic, 'load_guides', lambda: (guide('퇴직금 3년 월급 300', 1, kind=howto_topic.CASE),))
    seen = {}

    def fake_draft(*a, kind_rules='', **k):
        seen['rules'] = kind_rules
        return None

    monkeypatch.setattr(pipeline, 'draft_article', fake_draft)
    pipeline.compose_howto_post(*fake_llm({})[:1], MODELS, MON, (), get=lambda url: PAGE, limit=1)
    assert seen['rules'] == pipeline.howto_kind_rules(howto_topic.CASE)


def test_examples_are_appended_after_official_text_and_parsed(tmp_path):
    g = howto_topic.HowtoGuide('3년 근무 퇴직금', '생활경제', 'x', (howto_topic.Source('기관', 'https://www.example.go.kr/1'),),
                               kind=howto_topic.CASE, examples=('월급 300만 원 → 약 8,812,388원',))
    article = howto_topic.build_article(g, lambda c: make_article(), OCT)
    assert article.text.index('원문 본문입니다') < article.text.index('--- 계산 예시')
    assert '- 월급 300만 원 → 약 8,812,388원' in article.text
    path = tmp_path / 'g.json'
    path.write_text(json.dumps([{'query': 'q', 'slug': 's', 'examples': ['a', ' ', 'b'],
                                 'sources': [{'name': 'n', 'url': 'https://www.nts.go.kr/x'}]}]), encoding='utf-8')
    assert howto_topic.load_guides(path)[0].examples == ('a', 'b')


def test_case_guides_carry_precomputed_examples():
    cases = [g for g in howto_topic.load_guides() if g.kind == howto_topic.CASE]
    assert sum(1 for g in cases if g.examples) >= 12


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
    assert seen['only'] == topics.MONEY  # 2026-10-18: AI·IT 뉴스는 쓰지 않는다


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


@pytest.mark.parametrize('at, want', [((8, 0), howto_topic.GUIDE), ((20, 0), howto_topic.CASE)])
def test_run_passes_slot_kind_to_howto_post(monkeypatch, tmp_path, at, want):
    monkeypatch.setattr(history, 'DEFAULT_HISTORY_PATH', tmp_path / 'history.json')
    monkeypatch.setattr(pipeline.providers, 'build_text_backend', lambda p, env: None)
    monkeypatch.setattr(pipeline.providers, 'build_image_backend', lambda p, env: None)
    monkeypatch.setattr(pipeline, 'attach_hero_image', lambda c, *a, **k: c)
    monkeypatch.setattr(pipeline, 'write_preview', lambda c: 'preview.html')
    seen = {}

    def howto(*a, want='', **k):
        seen['want'] = want
        return pipeline.Composed(make_article(), make_draft())

    monkeypatch.setattr(pipeline, 'compose_howto_post', howto)
    target = pipeline.Target('slot', datetime(2026, 10, 19, *at, tzinfo=KST), 'preview')  # 월요일
    monkeypatch.setattr(pipeline, 'plan_targets', lambda *a, **k: [target])
    env = {'PUBLISH_TIMES': '08:00,20:00'}
    result = pipeline.run('preview', None, env, providers.load_ai_settings({}), None, now=NOW)
    assert result.outcome == 'success' and seen['want'] == want


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


def test_new_kind_guides_come_before_old_long_tail_guides():
    """2026-10-18: 계산 사례·숫자 변경 글감을 목록 앞에, 기존 롱테일 안내형은 그 뒤에 남는다."""
    guides = howto_topic.load_guides()
    assert guides[0].kind != howto_topic.GUIDE and len(guides) >= 90
    assert any(g.query == '실업급여 자진퇴사 조건' and g.kind == howto_topic.GUIDE for g in guides)
    assert howto_topic.order_guides(guides, (), MON)[0].kind == howto_topic.CASE
    assert howto_topic.order_guides(guides, (), THU)[0].kind == howto_topic.CHANGE


def test_guide_is_used_when_blogger_truncated_the_post_slug():
    """2026-10-11: Blogger가 긴 글 주소를 잘라 슬러그가 글감 슬러그의 앞부분만 남아도 쓴 글감으로 센다."""
    shared = howto_topic.Source('찾기쉬운 생활법령정보 「구직급여」', 'https://easylaw.go.kr/y')
    other = howto_topic.Source('다른 기관', 'https://easylaw.go.kr/z')
    first = howto_topic.HowtoGuide('실업급여 자진퇴사 조건', '생활경제', 'unemployment-benefit-voluntary-resignation', (shared,))
    second = howto_topic.HowtoGuide('배우자 출산휴가 급여 신청', '생활경제', 'spouse-maternity-leave-pay-apply', (shared, other))
    past = (make_entry(source_url=shared.url, url='https://www.woodyway88.com/2026/10/unemployment-benefit-voluntary.html'),)
    assert howto_topic.order_guides([first, second], past, OCT) == [second, first]


def test_history_entry_records_calculator_id_for_tools_site():
    """2026-10-18: 계산기 사이트가 발행 기록의 tool로 관련 글을 자동으로 단다."""
    tool = tools.find_tool(tools.load_tools(), 'severance-pay-calculator')
    composed = pipeline.Composed(make_article(), make_draft(), tool=tool)
    entry = pipeline.make_entry(composed, {'id': '1', 'url': 'https://www.woodyway88.com/a.html', 'status': 'LIVE'}, '2026-10-19')
    assert entry.tool == 'severance-pay-calculator'
    assert pipeline.make_entry(pipeline.Composed(make_article(), make_draft()), {'id': '2', 'status': 'LIVE'}, '2026-10-19').tool == ''
    real = Path(__file__).resolve().parent.parent / 'data' / 'history.json'
    assert all('tool' in row for row in json.loads(real.read_text(encoding='utf-8')))


def test_new_kind_posts_get_kind_label_for_blog_menu(monkeypatch):
    monkeypatch.setattr(howto_topic, 'load_guides', lambda: (guide('2027 최저임금 월급', 1, kind=howto_topic.CHANGE, area='생활경제'),))
    llm, _ = fake_llm({'PostDraft': [howto_draft(title='2027 최저임금 월급, 주 40시간이면 223만원', meta_description='2027 최저임금 월급은 223만원')],
                       'FactCheck': [OK]})
    composed = pipeline.compose_howto_post(llm, MODELS, THU, (), get=lambda url: PAGE, is_alive=ALIVE)
    assert composed.draft.labels == [topics.HOWTO_LABEL, '생활경제', '바뀌는제도']


# --- 지식창고 조사 연결 (2026-10-21) ---

TUE = datetime(2026, 10, 20, 12, 0, tzinfo=KST)  # 화요일 = 일정 칸
SUNEUNG_HINT = {'topic': '수능 일정', 'keywords': ['수능'], 'angle': '일정형',
                'howto': {'query': '2027 수능 시간표', 'kind': 'schedule', 'slug': 'suneung-2027-timetable', 'area': '생활경제',
                          'sources': [{'name': '한국교육과정평가원 수능 시행 안내', 'url': 'https://www.suneung.re.kr/'}]}}


def test_research_howto_goes_first_when_kind_matches_today():
    listed = guide('2027 설 연휴', 3, kind=howto_topic.SCHEDULE)
    order = howto_topic.order_guides([listed], (), TUE, [SUNEUNG_HINT])
    assert order[0].query == '2027 수능 시간표'
    assert order[1] == listed


def test_research_howto_waits_on_other_kind_days():
    case = guide('퇴직금 3년 월급 300', 4, kind=howto_topic.CASE)
    order = howto_topic.order_guides([case], (), MON, [SUNEUNG_HINT])
    assert order[0] == case  # 월요일은 계산 사례 칸 — 요일 순환이 우선


def test_listed_guide_matching_research_keyword_goes_first_within_kind():
    holiday = guide('2027 설 연휴', 3, kind=howto_topic.SCHEDULE)
    tax = guide('종합부동산세 납부기한', 5, kind=howto_topic.SCHEDULE)
    hint = {'topic': '종부세 고지', 'keywords': ['종합부동산세', '종부세']}
    assert howto_topic.order_guides([holiday, tax], (), TUE, [hint])[0] == tax
    assert howto_topic.order_guides([holiday, tax], (), TUE)[0] == holiday  # 조사 없으면 목록 순서


def test_research_howto_already_written_is_not_repeated():
    past = (make_entry(url='https://www.woodyway88.com/2026/10/suneung-2027-timetable.html'),)
    listed = guide('2027 설 연휴', 3, kind=howto_topic.SCHEDULE)
    assert howto_topic.order_guides([listed], past, TUE, [SUNEUNG_HINT])[0] == listed


def test_research_howto_without_https_source_is_ignored():
    bad = {'topic': 'x', 'keywords': ['x'], 'howto': {'query': '무출처 글감', 'kind': 'schedule', 'sources': []}}
    assert howto_topic.hint_guides([bad]) == ()
