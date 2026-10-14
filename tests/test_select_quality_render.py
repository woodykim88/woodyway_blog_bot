import pytest

from src import history, quality, renderer, selector
from src.llm import LLMError
from src.models import Candidate, CandidateScore, ClaimCheck, FactCheck, GlossaryItem, Section, SelectionResult, ShortNews
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
        CandidateScore(index=0, score=5, reason='r', one_line='a', story='s0'),
        CandidateScore(index=1, score=9, reason='r', one_line=' b ', story='s1'),
        CandidateScore(index=2, score=7, reason='r', one_line='c', story='s2'),
        CandidateScore(index=9, score=10, reason='r', one_line='범위 밖', story='s9'),
        CandidateScore(index=2, score=10, reason='r', one_line='중복 index', story='s2b'),
    ])
    llm, client = fake_llm({'SelectionResult': [result]})
    ranked = selector.rank_candidates(llm, 'fast', candidates, 'AI활용')
    assert [(r.candidate.title, r.score, r.one_line) for r in ranked] == [
        ('후보 제목 1', 9, 'b'),
        ('후보 제목 2', 7, 'c'),
    ]
    assert '오늘의 테마: AI활용' in client.requests[0]['input']


def test_rank_candidates_keeps_one_candidate_per_story():
    candidates = [make_candidate(i) for i in range(3)]
    result = SelectionResult(scores=[
        CandidateScore(index=0, score=8, reason='r', one_line='a', story='Microsoft Copilot 개편'),
        CandidateScore(index=1, score=9, reason='r', one_line='b', story='Microsoft의 copilot 개편'),
        CandidateScore(index=2, score=7, reason='r', one_line='c', story='엑셀 새 기능'),
    ])
    llm, _ = fake_llm({'SelectionResult': [result]})
    ranked = selector.rank_candidates(llm, 'fast', candidates, '자유 주제')
    assert [r.candidate.title for r in ranked] == ['후보 제목 1', '후보 제목 2']


def _one_score():
    return SelectionResult(scores=[CandidateScore(index=0, score=8, reason='r', one_line='a', story='s0')])


def test_rank_candidates_tells_llm_recent_titles():
    llm, client = fake_llm({'SelectionResult': [_one_score()]})
    selector.rank_candidates(llm, 'fast', [make_candidate(0)], '자유 주제', recent_titles=('고속도로 재진입 요금, 15분 안이면 900원 면제',))
    assert '최근 발행한 글 제목' in client.requests[0]['input']
    assert '고속도로 재진입 요금, 15분 안이면 900원 면제' in client.requests[0]['input']


def test_rank_candidates_tells_llm_trending_keywords():
    llm, client = fake_llm({'SelectionResult': [_one_score()]})
    selector.rank_candidates(llm, 'fast', [make_candidate(0)], '자유 주제', trending=('장마', '코스피 6870 마감'))
    assert '많이 검색되는 키워드' in client.requests[0]['input']
    assert '- 코스피 6870 마감' in client.requests[0]['input']


def test_rank_candidates_omits_extra_sections_when_none():
    llm, client = fake_llm({'SelectionResult': [_one_score()]})
    selector.rank_candidates(llm, 'fast', [make_candidate(0)], '자유 주제')
    assert '최근 발행한 글 제목' not in client.requests[0]['input']
    assert '많이 검색되는' not in client.requests[0]['input']


def test_rank_candidates_drops_candidates_already_covered_by_recent_posts():
    candidates = [make_candidate(i) for i in range(3)]
    result = SelectionResult(scores=[
        CandidateScore(index=0, score=10, reason='r', one_line='a', story='고속도로 재진입', already_covered=True),
        CandidateScore(index=1, score=9, reason='r', one_line='b', story='청약통장'),
        CandidateScore(index=2, score=8, reason='r', one_line='c', story='전기요금'),
    ])
    llm, _ = fake_llm({'SelectionResult': [result]})
    ranked = selector.rank_candidates(llm, 'fast', candidates, '자유 주제', recent_titles=('고속도로 재진입 요금',))
    assert [r.candidate.title for r in ranked] == ['후보 제목 1', '후보 제목 2']


def test_covered_candidate_also_blocks_same_story_siblings():
    candidates = [make_candidate(i) for i in range(2)]
    result = SelectionResult(scores=[
        CandidateScore(index=0, score=10, reason='r', one_line='a', story='고속도로 재진입 요금', already_covered=True),
        CandidateScore(index=1, score=9, reason='r', one_line='b', story='고속도로 재진입 요금'),
    ])
    llm, _ = fake_llm({'SelectionResult': [result]})
    assert selector.rank_candidates(llm, 'fast', candidates, '자유 주제', recent_titles=('x',)) == []


def test_candidate_score_schema_requires_every_field_for_strict_output():
    schema = CandidateScore.model_json_schema()
    assert set(schema['required']) == set(schema['properties'])


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
    assert len(quality.body_text(make_draft())) >= quality.MIN_BODY_CHARS
    assert not hasattr(quality, 'MAX_BODY_CHARS')  # 분량 상한 없음: 품질과 클릭률이 기준


@pytest.mark.parametrize('overrides, expected', [
    ({'key_points': ['a', 'b']}, '핵심 내용 불릿'),
    ({'sections': [Section(heading=f'h{i}', body='가' * 600, tip='t') for i in range(6)]}, '5개까지'),
    ({'hook': '회의록 AI 요약 기능이 새로 나왔습니다. 무엇이 달라졌는지 정리했습니다.'}, '도입부'),
    ({'hook': '회의가 끝나면 늘 한숨부터 나옵니다. 오늘은 회의록 요약 도구를 살펴봅니다.'}, '도입부'),
    ({'sections': [Section(heading='a', body='가' * 800, tip=' '), Section(heading='b', body='나' * 800, tip='t')]}, '꿀팁'),
    ({'glossary': [GlossaryItem(term='STT', meaning=' ')]}, '용어사전'),
    ({'action_items': []}, '내일 바로 써먹기'),
    ({'labels': []}, '라벨'),
    ({'title': '가' * 41}, '제목이'),
    ({'meta_description': '가' * 151}, '메타 설명'),
    ({'why_it_matters': '이건 충격적인 소식입니다.'}, '과장'),
    ({'title': '회의록 정리에 쓰는 AI 도구 비교'}, '검색 키워드'),
    ({'meta_description': '정리 시간을 줄이는 방법'}, '메타 설명에 검색 키워드'),
    ({'title': '회의록 AI 요약 방법 3가지'}, '틀'),
    ({'title': '회의록 AI 요약, 구독비 점검 3단계'}, '틀'),
])
def test_quality_gate_rule_failures(overrides, expected):
    llm, client = fake_llm({})
    report = quality.run_quality_gate(llm, 'fast', make_draft(**overrides), make_article(), (), ALIVE)
    assert not report.passed
    assert any(expected in issue for issue in report.issues)
    assert client.requests == []  # 규칙 검사 실패 시 LLM 사실 검증은 호출하지 않음


def test_check_copying_flags_long_verbatim_phrase():
    article = make_article()
    copied = '원문 본문입니다. 원문 본문입니다. 원문 본문입니다. 원문 본문입니다.'
    assert any('그대로' in i for i in quality.check_copying(make_draft(why_it_matters=copied), article))
    assert quality.check_copying(make_draft(), article) == []


def test_check_copying_ignores_shared_english_product_names():
    article = make_article()
    article = type(article)(article.candidate, article.source_url, article.text + ' Microsoft Copilot Managed Runtime')
    draft = make_draft(why_it_matters='Microsoft Copilot Managed Runtime은 회사 전산팀이 먼저 검토할 대상입니다.')
    assert quality.check_copying(draft, article) == []


def test_keyword_match_ignores_spaces():
    draft = make_draft(title='회의록AI요약, 1분이면 끝나는 이유', meta_description='회의록 AI요약 활용법')
    assert quality.check_seo(draft) == []


def test_fact_check_text_includes_hook():
    assert make_draft().hook in quality.fact_check_text(make_draft())


def test_quality_gate_length_bounds():
    short = make_draft(sections=[Section(heading='a', body='짧음', tip='t'), Section(heading='b', body='짧음', tip='t')])
    assert any('너무 짧' in i for i in quality.check_length(short))


def test_quality_gate_dead_source_and_duplicate_title():
    past = (make_entry(title='회의록 AI 요약, 30분 걸리던 정리가 1분이면 끝나는 이유'),)
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
    assert '한 줄 요약' not in html
    assert html.index('회의는 30분 만에') < html.index('핵심 내용')  # 도입부가 맨 앞
    for text in ('핵심 내용', '배경', '작동 방식', '그래서 나에게 무엇이 달라질까', '내일 바로 써먹기',
                 'https://example.com/original', 'GeekNews', '오늘의 짧은 소식', '관련 글',
                 'https://img.example.com/a.webp'):
        assert text in html
    assert 'AI의 도움' not in html  # AI 작성 고지는 붙이지 않는다 (사용자 결정 2026-09-27)
    assert html.count('<p>회의록') == 6  # 섹션 3개 x 문단 2개
    assert '<h2>배경</h2>' in html and '<h2>작동 방식</h2>' in html  # 섹션 소제목은 h2 (검색 노출)
    assert html.count('<h2>') >= 3


def test_render_post_hook_tips_and_glossary():
    html = renderer.render_post(make_draft(), make_article())
    assert html.count('<p style="font-size:1.2em') == 2  # 도입부 문단 2개
    assert '회의 시작 전에 녹음 버튼부터 누르세요.' in html and '💡' in html
    assert '용어사전' in html and 'STT' in html
    assert html.index('내일 바로 써먹기') < html.index('용어사전') < html.index('출처')


def test_render_post_without_glossary_has_no_glossary_heading():
    assert '용어사전' not in renderer.render_post(make_draft(glossary=[]), make_article())


def test_fact_check_text_includes_glossary():
    assert 'STT' in quality.fact_check_text(make_draft())


def test_render_post_direct_news_source_has_no_geeknews_via():
    news = make_article(Candidate(title='t', link='https://www.hankyung.com/article/1', summary='',
                                  published=make_candidate().published, source='한국경제', direct=True))
    html = renderer.render_post(make_draft(), news)
    assert '한국경제' in html
    assert 'GeekNews' not in html


def test_format_candidates_shows_source():
    news = Candidate(title='뉴스 제목', link='https://x', summary='s', published=make_candidate().published,
                     source='한국경제', direct=True)
    assert '(한국경제) 뉴스 제목' in selector.format_candidates([news])


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
    assert html.rstrip().endswith('</div>') and html.rindex(url) > html.index('출처')  # 인포크 안내가 글의 마지막
    assert 'inpock' not in renderer.render_post(make_draft(), make_article())


def test_fact_check_excludes_action_items():
    draft = make_draft(action_items=['폴더 3개를 만드세요'])
    llm, client = fake_llm({'FactCheck': [FactCheck(claims=[])]})
    quality.check_facts(llm, 'fast', draft, make_article())
    sent = client.requests[0]['input']
    assert '폴더 3개를 만드세요' not in sent
    assert draft.key_points[0] in sent and draft.title in sent


# --- 썸네일 문구 ---

@pytest.mark.parametrize('overrides, expected', [
    ({'thumbnail_text': '가나다라마바사\n아자차카타파하거'}, '14자'),
    ({'thumbnail_text': '월 3만원\n아끼는\n설정'}, '두 줄'),
    ({'thumbnail_text': '월 3만원\n아끼는 설정', 'thumbnail_highlight': '5만원'}, '강조'),
    ({'thumbnail_text': '월 3만원\n아끼는 설정', 'thumbnail_highlight': '월 3만원 아끼는'}, '6자'),
    ({'thumbnail_text': '충격적인\n요금 인상'}, '과장'),
    ({'thumbnail_text': '무조건\n끄세요', 'thumbnail_highlight': '무조건'}, '과장'),
])
def test_check_thumbnail_failures(overrides, expected):
    issues = quality.check_thumbnail(make_draft(**overrides))
    assert any(expected in issue and '썸네일' in issue for issue in issues), issues


def test_check_thumbnail_passes_good_and_empty_text():
    assert quality.check_thumbnail(make_draft(thumbnail_text='이 기능 끄면\n배터리 2배', thumbnail_highlight='2배')) == []
    assert quality.check_thumbnail(make_draft(thumbnail_text='코파일럿\n24시간 일한다?', thumbnail_highlight='')) == []
    assert quality.check_thumbnail(make_draft(thumbnail_text='', thumbnail_highlight='')) == []


def test_quality_gate_runs_thumbnail_check_before_fact_check():
    llm, client = fake_llm({})
    report = quality.run_quality_gate(llm, 'fast', make_draft(thumbnail_text='대박\n요금'), make_article(), (), ALIVE)
    assert not report.passed and any('썸네일' in i for i in report.issues)
    assert client.requests == []


def test_fact_check_text_includes_thumbnail_text():
    draft = make_draft(thumbnail_text='회의록 정리\n1분이면 끝')
    assert '회의록 정리 1분이면 끝' in quality.fact_check_text(draft)


def test_related_posts_prefers_more_shared_labels_and_returns_three():
    past = ()
    for title, labels in [('약한', ('AI활용',)), ('강한', ('AI활용', '업무생산성')), ('무관', ('경제',)),
                          ('최신약한', ('업무생산성',)), ('다른약한', ('AI활용',))]:
        past = history.append_entry(past, make_entry(title=title, labels=labels))
    got = [e.title for e in history.related_posts(past, ('AI활용', '업무생산성'))]
    assert got == ['강한', '다른약한', '최신약한']  # 공유 라벨 많은 순, 같으면 최신순, 기본 3개


def test_format_candidates_tags_trending_keyword():
    news = make_candidate(title='연말정산 간소화 개통')
    text = selector.format_candidates([news], ('연말정산',))
    assert '[지금 검색됨: 연말정산]' in text
    assert '[지금 검색됨' not in selector.format_candidates([news], ('장마',))


def test_related_posts_prefers_same_topic_over_labels():
    past = (
        make_entry(title='실업급여 신청 방법, 자격 한 번에', labels=('지원금·혜택',), url='https://www.woodyway88.com/p0.html'),
        make_entry(title='청년미래적금 가입 조건', labels=('재테크', '지원금·혜택'), url='https://www.woodyway88.com/p1.html'),
        make_entry(title='퇴직금 계산 방법', labels=('재테크',), url='https://www.woodyway88.com/p2.html'),
    )
    got = [e.title for e in history.related_posts(past, ('재테크', '지원금·혜택'), topic='실업급여 계산 실업급여 하루 상한')]
    assert got[0] == '실업급여 신청 방법, 자격 한 번에'  # 라벨 1개뿐이어도 같은 주제가 먼저


def test_featured_link_only_for_same_topic():
    draft = make_draft(search_keyword='회의록 AI 요약')
    same = make_entry(title='회의록 자동 정리 도구 비교', labels=('AI활용',), url='https://www.woodyway88.com/p0.html')
    other = make_entry(title='연차 발생 기준', labels=('AI활용',), url='https://www.woodyway88.com/p1.html')
    html = renderer.render_post(draft, make_article(), related=(same, other))
    assert '함께 보면 좋은 글: <a href="' + same.url in html
    assert '함께 보면 좋은 글' not in renderer.render_post(draft, make_article(), related=(other,))
