import pytest

from src import history, quality, renderer, selector
from src.llm import LLMError
from src.models import CandidateScore, ClaimCheck, FactCheck, Section, SelectionResult, ShortNews
from tests.helpers import fake_llm, make_article, make_candidate, make_draft, make_entry

ALIVE = lambda url: True
DEAD = lambda url: False


# --- LLMClient ---

def test_llm_client_enforces_call_limit():
    llm, _ = fake_llm({'FactCheck': [FactCheck(claims=[])] * 3}, max_calls=2)
    llm.parse('m', 'i', 'u', FactCheck)
    llm.parse('m', 'i', 'u', FactCheck)
    with pytest.raises(LLMError):
        llm.parse('m', 'i', 'u', FactCheck)


def test_llm_client_raises_when_unparsed():
    llm, _ = fake_llm({'FactCheck': [None]})
    with pytest.raises(LLMError):
        llm.parse('m', 'i', 'u', FactCheck)


# --- selector ---

def test_is_duplicate_by_url_and_similar_title():
    past = (make_entry(title='회의록 정리 30분을 1분으로', geeknews_url='https://news.hada.io/topic?id=1000'),)
    assert selector.is_duplicate(make_candidate(0), past)  # 같은 URL
    assert selector.is_duplicate(make_candidate(5, title='회의록 정리 30분을 1분으로!'), past)
    assert not selector.is_duplicate(make_candidate(6, title='완전히 다른 주제의 소식'), past)


def test_rank_candidates_filters_low_scores_and_sorts():
    candidates = [make_candidate(i) for i in range(4)]
    result = SelectionResult(scores=[
        CandidateScore(index=0, score=5, reason='r', one_line='a'),
        CandidateScore(index=1, score=9, reason='r', one_line=' b '),
        CandidateScore(index=2, score=7, reason='r', one_line='c'),
        CandidateScore(index=9, score=10, reason='r', one_line='범위 밖'),
        CandidateScore(index=2, score=10, reason='r', one_line='중복 index'),
    ])
    llm, client = fake_llm({'SelectionResult': [result]})
    ranked = selector.rank_candidates(llm, 'fast', candidates, 'AI활용')
    assert [(r.candidate.title, r.score, r.one_line) for r in ranked] == [
        ('후보 제목 1', 9, 'b'),
        ('후보 제목 2', 7, 'c'),
    ]
    assert '오늘의 테마: AI활용' in client.requests[0]['input']


def test_rank_candidates_empty_does_not_call_llm():
    llm, client = fake_llm({})
    assert selector.rank_candidates(llm, 'fast', [], 'AI활용') == []
    assert client.requests == []


# --- quality ---

def test_quality_gate_passes_good_draft():
    llm, _ = fake_llm({'FactCheck': [FactCheck(claims=[])]})
    report = quality.run_quality_gate(llm, 'fast', make_draft(), make_article(), (), ALIVE)
    assert report.passed, report.issues


def test_sample_draft_is_within_length():
    assert quality.MIN_BODY_CHARS <= len(quality.body_text(make_draft())) <= quality.MAX_BODY_CHARS


@pytest.mark.parametrize('overrides, expected', [
    ({'key_points': ['a', 'b']}, '핵심 내용 불릿'),
    ({'sections': [Section(heading='h', body='b' * 1500)]}, '자세히 보기 섹션'),
    ({'action_items': []}, '내일 바로 써먹기'),
    ({'labels': []}, '라벨'),
    ({'title': '가' * 41}, '제목이'),
    ({'meta_description': '가' * 151}, '메타 설명'),
    ({'why_it_matters': '이건 충격적인 소식입니다.'}, '과장'),
])
def test_quality_gate_rule_failures(overrides, expected):
    llm, client = fake_llm({})
    report = quality.run_quality_gate(llm, 'fast', make_draft(**overrides), make_article(), (), ALIVE)
    assert not report.passed
    assert any(expected in issue for issue in report.issues)
    assert client.requests == []  # 규칙 검사 실패 시 LLM 사실 검증은 호출하지 않음


def test_quality_gate_length_bounds():
    short = make_draft(sections=[Section(heading='a', body='짧음'), Section(heading='b', body='짧음')])
    assert any('분량' in i for i in quality.check_length(short))


def test_quality_gate_dead_source_and_duplicate_title():
    past = (make_entry(title='회의록 정리 30분을 1분으로 줄이는 방법 3가지'),)
    llm, _ = fake_llm({})
    report = quality.run_quality_gate(llm, 'fast', make_draft(), make_article(), past, DEAD)
    assert any('출처 링크' in i for i in report.issues)
    assert any('제목이 비슷' in i for i in report.issues)


def test_quality_gate_fact_check_counts_only_unsupported_facts():
    result = FactCheck(claims=[
        ClaimCheck(sentence='매출이 30% 늘었습니다', kind='unsupported_fact', reason='원문에 수치 없음'),
        ClaimCheck(sentence='회의 전에 써보세요', kind='opinion_or_advice', reason='조언'),
        ClaimCheck(sentence='새 기능이 나왔습니다', kind='supported_fact', reason='원문 2문단'),
    ])
    llm, _ = fake_llm({'FactCheck': [result]})
    report = quality.run_quality_gate(llm, 'fast', make_draft(), make_article(), (), ALIVE)
    assert report.issues == ('원문에 근거 없는 사실: "매출이 30% 늘었습니다" (원문에 수치 없음)',)


def test_quality_gate_fact_check_passes_with_only_opinions():
    result = FactCheck(claims=[ClaimCheck(sentence='써보세요', kind='opinion_or_advice', reason='조언')])
    llm, _ = fake_llm({'FactCheck': [result]})
    assert quality.run_quality_gate(llm, 'fast', make_draft(), make_article(), (), ALIVE).passed


# --- renderer ---

def test_render_post_contains_sections_and_sources():
    html = renderer.render_post(
        make_draft(),
        make_article(),
        short_news=[ShortNews('짧은 소식', '한 줄', 'https://news.hada.io/topic?id=2')],
        related=[make_entry(title='관련 글')],
        hero_image_url='https://img.example.com/a.webp',
    )
    for text in ('한 줄 요약', '핵심 내용', '배경', '작동 방식', '왜 중요한가', '내일 바로 써먹기',
                 'https://example.com/original', 'GeekNews', '오늘의 짧은 소식', '관련 글',
                 'https://img.example.com/a.webp', 'AI의 도움'):
        assert text in html
    assert html.count('<p>회의록') == 4  # 섹션 2개 x 문단 2개


def test_render_post_escapes_llm_output():
    draft = make_draft(key_points=['<script>alert(1)</script>', 'b', 'c'])
    html = renderer.render_post(draft, make_article())
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


def test_render_post_omits_optional_blocks():
    html = renderer.render_post(make_draft(), make_article())
    assert '오늘의 짧은 소식' not in html and '함께 읽으면' not in html and '<img' not in html


# --- history ---

def test_history_roundtrip_and_helpers(tmp_path):
    path = tmp_path / 'data' / 'history.json'
    assert history.load_history(path) == ()
    past = history.append_entry((), make_entry(title='하나', labels=('AI활용',)))
    past = history.append_entry(past, make_entry(title='둘', labels=('커리어',), publish_date='2026-09-25'))
    history.save_history(past, path)
    loaded = history.load_history(path)
    assert [e.title for e in loaded] == ['하나', '둘']
    assert [e.slot for e in loaded] == ['2026-09-24T20:00', '2026-09-25T20:00']  # 예전 기록은 20시 슬롯
    assert history.done_slots(loaded) == {'2026-09-24T20:00', '2026-09-25T20:00'}
    assert history.has_entry_for(loaded, '2026-09-25')
    assert not history.has_entry_for(loaded, '2026-09-26')
    assert [e.title for e in history.related_posts(loaded, ('AI활용', '툴추천'))] == ['하나']


def test_render_post_footer_link():
    url = 'https://link.inpock.co.kr/shoperwoody'
    html = renderer.render_post(make_draft(), make_article(), footer_url=url)
    assert f'href="{url}"' in html
    assert html.index(url) < html.index('AI의 도움')  # AI 고지문 바로 위
    assert 'inpock' not in renderer.render_post(make_draft(), make_article())


def test_fact_check_excludes_action_items():
    draft = make_draft(action_items=['폴더 3개를 만드세요'])
    llm, client = fake_llm({'FactCheck': [FactCheck(claims=[])]})
    quality.check_facts(llm, 'fast', draft, make_article())
    sent = client.requests[0]['input']
    assert '폴더 3개를 만드세요' not in sent
    assert draft.key_points[0] in sent and draft.title in sent
