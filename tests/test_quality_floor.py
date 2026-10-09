"""2026-10-03 개선: 12:30 칸 예비 사용법 글, 분량·h2 하한, 관련 기사 비교, 계산기 링크."""
from pathlib import Path

from src import fetcher, guide_topic, quality, rescue, selector, thumbnail, tools, topics, writer
from src import main as pipeline
from src.models import Candidate, CandidateScore, FactCheck, Reference, Section, SelectionResult
from src.renderer import render_post
from tests.helpers import NOW, fake_llm, make_article, make_candidate, make_draft, make_entry

MODELS = pipeline.Models(writer='writer-model', fast='fast-model')
OK = FactCheck(claims=[])
ALIVE = lambda url: True  # noqa: E731
GUIDE_HTML = '<html><body><article>' + ''.join(
    f'<p>CLAUDE.md 파일 사용법 {i}번째 문단입니다. 프로젝트 지침을 적어 두면 매번 다시 설명하지 않아도 됩니다.</p>' for i in range(30)
) + '</article></body></html>'


def short_draft(**overrides):
    para = '짧은 문단입니다. ' * 8
    sections = [Section(heading=f'궁금증 {i}', body=para, tip='바로 해 보세요.') for i in range(2)]
    return make_draft(sections=sections, **overrides)


# --- 분량·h2 하한 ---

def test_floor_flags_short_body_and_too_few_sections_as_soft():
    llm, client = fake_llm({'FactCheck': [OK]})
    report = quality.run_quality_gate(llm, 'fast', short_draft(), make_article(), (), ALIVE)
    assert not report.passed and report.only_soft
    assert any('h2' in i for i in report.issues) and any('2500' in i for i in report.issues)
    assert len(client.requests) == 1  # 하한 미달이어도 사실검증은 한다 (발행할 수 있는지 알아야 함)


def test_floor_passes_full_draft():
    assert quality.check_floor(make_draft()) == []
    assert quality.body_length(make_draft()) >= quality.MIN_BODY_CHARS


def test_hard_issue_is_not_only_soft():
    llm, _ = fake_llm({})
    report = quality.run_quality_gate(llm, 'fast', short_draft(labels=[]), make_article(), (), ALIVE)
    assert not report.only_soft


def test_short_draft_is_rewritten_once_then_published_anyway(capsys):
    llm, client = fake_llm({'PostDraft': [short_draft(), short_draft()], 'FactCheck': [OK, OK]})
    draft = pipeline.draft_article(llm, MODELS, make_article(), '테마', (), ALIVE)
    assert draft is not None  # 하한 때문에 칸을 비우지 않는다
    assert '분량 미달 발행' in capsys.readouterr().out
    assert any('2500' in r['input'] for r in client.requests if r['model'] == 'writer-model')  # 재작성 피드백


def test_short_then_full_draft_uses_the_full_one():
    llm, _ = fake_llm({'PostDraft': [short_draft(), make_draft()], 'FactCheck': [OK, OK]})
    draft = pipeline.draft_article(llm, MODELS, make_article(), '테마', (), ALIVE)
    assert len(draft.sections) == 3


def test_rescue_accepts_draft_that_only_falls_short_after_removal():
    bad = '이 기능은 내년 1월 전 세계에 동시에 출시됩니다.'
    draft = short_draft(why_it_matters=f'회의가 많은 팀일수록 체감이 큽니다. {bad}')
    llm, _ = fake_llm({'FactCheck': [OK]})
    fixed = rescue.rescue(llm, 'fast', draft, make_article(), [bad], (), ALIVE)
    assert fixed is not None and bad not in fixed.why_it_matters


# --- 여러 기사 비교 ---

def test_selector_attaches_same_story_article_from_other_source():
    a = Candidate('정부, 청년 월세 지원 확대', 'https://a.com/1', 's', NOW, source='한국경제', direct=True)
    b = Candidate('청년 월세 지원금 20만원으로', 'https://b.com/2', 's', NOW, source='이투데이', direct=True)
    c = Candidate('다른 소식', 'https://c.com/3', 's', NOW, source='한국경제', direct=True)
    llm, _ = fake_llm({'SelectionResult': [SelectionResult(scores=[
        CandidateScore(index=0, score=9, reason='r', one_line='l', story='청년 월세 지원'),
        CandidateScore(index=1, score=5, reason='r', one_line='l', story='청년 월세지원'),
        CandidateScore(index=2, score=7, reason='r', one_line='l', story='다른 소식'),
    ])]})
    ranked = selector.rank_candidates(llm, 'fast', [a, b, c], '테마')
    assert ranked[0].candidate == a and ranked[0].related == (b,)


def test_similar_titles_fallback_needs_two_shared_words():
    main = make_candidate(title='엔비디아 실적 발표 주가 반응')
    pool = [main, make_candidate(1, '엔비디아 실적 예상치 상회'), make_candidate(2, '엔비디아 신제품')]
    assert [c.title for c in selector.similar_titles(main, pool)] == ['엔비디아 실적 예상치 상회']


def test_with_references_appends_related_articles_and_sources():
    html = '<html><body><article>' + '<p>관련 기사 본문 문단입니다. 숫자가 조금 다릅니다.</p>' * 30 + '</article></body></html>'
    related = Candidate('관련 기사 제목', 'https://b.com/2', 's', NOW, source='이투데이', direct=True)
    article = fetcher.with_references(lambda url: html, make_article(), [related])
    assert '관련 기사 1: 이투데이' in article.text and article.text.startswith(make_article().text)
    assert article.references == (Reference('이투데이', '관련 기사 제목', 'https://b.com/2'),)
    assert '비교·대조' in writer.build_writer_input(article, '테마')
    assert 'https://b.com/2' in render_post(make_draft(), article)


def test_with_references_keeps_article_when_related_page_fails():
    related = Candidate('t', 'https://b.com/2', 's', NOW, source='x', direct=True)
    assert fetcher.with_references(lambda url: '<p>짧음</p>', make_article(), [related]) == make_article()


# --- 12:30 칸: 스킬이 없으면 AI 도구 사용법 글 ---

def test_order_guides_prefers_unused_then_oldest_used():
    g = [guide_topic.Guide('Claude Code', f'k{i}', f'https://code.claude.com/docs/ko/{i}') for i in range(3)]
    past = (make_entry(source_url=g[1].url), make_entry(source_url=g[0].url))
    assert guide_topic.order_guides(g, past) == [g[2], g[1], g[0]]


def test_guide_list_is_https_official_docs():
    guides = guide_topic.load_guides()
    assert len(guides) >= 10
    assert all(g.url.startswith('https://') for g in guides)


def test_slot_falls_back_to_ai_guide_post_not_news(monkeypatch):
    monkeypatch.setattr(pipeline, 'compose_skill_post', lambda *a, **k: None)
    monkeypatch.setattr(pipeline, 'compose_post', lambda *a, **k: (_ for _ in ()).throw(AssertionError('news')))
    monkeypatch.setattr(guide_topic, 'load_guides', lambda: (guide_topic.Guide('Claude Code', '클로드 코드 CLAUDE.md 작성법', 'https://code.claude.com/docs/ko/memory'),))
    llm, client = fake_llm({'PostDraft': [make_draft(labels=['AI활용'])], 'FactCheck': [OK]})
    composed = pipeline.compose_slot_post(llm, MODELS, NOW, (), get=lambda url: GUIDE_HTML, is_alive=ALIVE)
    assert composed.category == topics.GUIDE
    assert composed.draft.labels[0] == topics.GUIDE_LABEL
    assert composed.article.source_url == 'https://code.claude.com/docs/ko/memory'
    assert composed.tool is None
    assert '사용법 글' in client.requests[0]['instructions']


def test_compose_guide_post_wraps_default_get(monkeypatch):
    monkeypatch.setattr(guide_topic, 'load_guides', lambda: (guide_topic.Guide('Gemini', '제미나이 젬', 'https://support.google.com/x'),))
    llm, _ = fake_llm({'PostDraft': [make_draft()], 'FactCheck': [OK]})
    composed = pipeline.compose_guide_post(llm, MODELS, NOW, (), get=lambda url: GUIDE_HTML, is_alive=ALIVE)
    assert composed is not None and composed.article.candidate.source == 'Gemini 공식 도움말'


def test_slot_uses_tech_news_only_as_last_resort(monkeypatch):
    monkeypatch.setattr(pipeline, 'compose_skill_post', lambda *a, **k: None)
    monkeypatch.setattr(pipeline, 'compose_guide_post', lambda *a, **k: None)
    seen = {}

    def news(*a, only=None, **k):
        seen['only'] = only
        return 'news'

    monkeypatch.setattr(pipeline, 'compose_post', news)
    assert pipeline.compose_slot_post(*fake_llm({})[:1], MODELS, NOW, ()) == 'news'
    assert seen['only'] == topics.TECH  # 생활경제 기사가 12:30 칸에 오지 않게


def test_guide_posts_do_not_count_toward_news_balance_and_get_louis():
    assert topics.category_of(make_entry(labels=('AI사용법', 'AI활용'))) == topics.SKILL
    assert thumbnail.mascot_for(topics.GUIDE) is not None


# --- 계산기 링크 ---

def money_draft(**overrides):
    return make_draft(title='기준금리 동결, 대출 이자 어떻게 될까', search_keyword='기준금리 동결',
                      meta_description='기준금리 동결 소식', labels=['재테크'], **overrides)


def test_calculator_matches_money_post_by_context():
    draft = money_draft(why_it_matters='변동금리 대출이라면 금리 인하 시점에 갈아타기를 따져 볼 만합니다.')
    tool = tools.match_tool(draft, tools.load_tools(), topics.MONEY)
    assert tool is not None and 'mortgage' in tool.url


def test_calculator_context_match_only_for_money_posts():
    draft = money_draft(why_it_matters='변동금리 대출이라면 금리 인하 시점에 갈아타기를 따져 볼 만합니다.')
    assert tools.match_tool(draft, tools.load_tools(), topics.TECH) is None
    assert tools.match_tool(money_draft(), tools.load_tools(), topics.MONEY) is None  # 문맥 낱말 1개뿐


def test_calculator_strong_keyword_in_meta_and_never_on_slot_posts():
    draft = make_draft(meta_description='K패스 환급 바뀐 점 정리')
    assert 'transit' in tools.match_tool(draft, tools.load_tools()).url
    assert tools.match_tool(draft, tools.load_tools(), topics.GUIDE) is None


def test_tools_json_lists_every_calculator_page():
    urls = {t.url for t in tools.load_tools()}
    pages = Path.home() / 'Projects' / '도구' / '정보마스터-계산기' / 'pages'
    expected = {f'https://tools.woodyway88.com/{p.name}/' for p in pages.iterdir()} if pages.exists() else set()
    assert expected <= urls
    assert len(urls) >= 3
