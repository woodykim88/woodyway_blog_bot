import pytest

from src import collector, topics
from src import main as pipeline
from src.models import CandidateScore, ClaimCheck, FactCheck, SelectionResult, Section
from tests.helpers import FIXTURES, NOW, fake_llm, make_draft, make_entry

TECH_PAST = (make_entry(labels=('AI활용',)),)  # AI·IT 1편 뒤에는 생활경제 차례 (2:1 규칙)

FEED = (FIXTURES / 'geeknews_feed.xml').read_text(encoding='utf-8')
NEWS_FEED_URLS = {s.url for s in collector.NEWS_SOURCES}
MONEY_FEED_URLS = {s.url for s in collector.MONEY_SOURCES}
EMPTY_FEED = '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'
MODELS = pipeline.Models(writer='writer-model', fast='fast-model')
ARTICLE_HTML = '<html><body><article>' + ''.join(
    f'<p>원문 기사의 {i}번째 문단입니다. 직장인에게 도움이 되는 충분히 긴 내용이 여기에 들어갑니다.</p>' for i in range(30)
) + '</article></body></html>'


def fake_get(broken_topics: set[str] = frozenset()):
    def get(url: str) -> str:
        if url.startswith(('https://trends.google.com/', 'https://whereispost.com/')):
            return ''  # 트렌드 신호는 없어도 발행에 영향이 없다
        if url == 'https://news.hada.io/rss/news':
            return FEED
        if url in NEWS_FEED_URLS or url in MONEY_FEED_URLS:
            return EMPTY_FEED
        if url.startswith('https://news.hada.io/topic?id='):
            topic_id = url.split('=')[1]
            if topic_id in broken_topics:
                return '<p>no title link</p>'
            return f"<a href='https://example.com/{topic_id}' class='topic-title-link'>t</a>"
        if url.startswith('https://example.com/'):
            return ARTICLE_HTML
        raise AssertionError(f'unexpected url {url}')
    return get




@pytest.fixture(autouse=True)
def tech_first(monkeypatch, request):
    """기존 테스트는 AI·IT 분야 흐름을 본다. 분야 선택 자체는 test_topics와 아래 분야 테스트에서 본다."""
    if 'money' not in request.node.name:
        monkeypatch.setattr(topics, 'choose_category', lambda past: topics.TECH)


def selection(*indexes_scores):
    return SelectionResult(scores=[
        CandidateScore(index=i, score=s, reason='r', one_line=f'한 줄 {i}', story=f'소식 {i}') for i, s in indexes_scores
    ])


OK = FactCheck(claims=[])


def test_compose_post_happy_path():
    llm, client = fake_llm({
        'SelectionResult': [selection((0, 9), (1, 8), (2, 7), (3, 6))],
        'PostDraft': [make_draft()],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)

    assert composed.article.source_url == 'https://example.com/34262'
    assert composed.draft.title == make_draft().title
    assert '오늘의 짧은 소식' in composed.html
    assert all(f'한 줄 {i}' in composed.html for i in (1, 2, 3))  # 메인 제외 상위 3건
    assert '한 줄 0' not in composed.html
    assert [r['model'] for r in client.requests] == ['fast-model', 'writer-model', 'writer-model', 'fast-model']  # 선별·작성·윤문·검증
    assert llm.calls == 4  # 선별·작성·윤문·검증


def test_compose_post_rewrites_once_with_feedback():
    llm, client = fake_llm({
        'SelectionResult': [selection((0, 9))],
        'PostDraft': [make_draft(action_items=[]), make_draft()],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)
    assert composed.draft.action_items
    assert any('검수에서 아래 이유로 탈락' in r['input'] for r in client.requests)


def test_compose_post_falls_back_to_next_topic_when_source_missing():
    llm, _ = fake_llm({
        'SelectionResult': [selection((0, 9), (1, 8))],
        'PostDraft': [make_draft()],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(
        llm, MODELS, NOW, (), get=fake_get(broken_topics={'34262'}), is_alive=lambda u: True
    )
    assert composed.article.source_url != 'https://example.com/34262'


def test_compose_post_raises_when_nothing_scores_high_enough():
    llm, _ = fake_llm({'SelectionResult': [selection((0, 3), (1, 2))] * 2})
    with pytest.raises(pipeline.PipelineError):
        pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)


def test_compose_post_relaxes_score_bar_instead_of_skipping_the_slot(capsys):
    """두 분야 모두 기준 점수 이상이 없으면 그중 가장 나은 글감으로 쓴다 (발행을 건너뛰지 않는다)."""
    llm, _ = fake_llm({
        'SelectionResult': [selection((0, 5), (1, 4)), selection((0, 5), (1, 4))],
        'PostDraft': [make_draft()],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)
    assert composed.draft.title == make_draft().title
    assert '기준을 낮춰' in capsys.readouterr().out


def test_compose_post_money_prefers_strict_tech_over_relaxing_money():
    llm, _ = fake_llm({
        'SelectionResult': [selection((0, 5)), selection((0, 9))],  # 생활경제 5점(기준 미달), AI·IT 9점
        'PostDraft': [make_draft()],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, TECH_PAST, get=money_get(), is_alive=lambda u: True)
    assert composed.category == topics.TECH  # 생활경제의 기준을 낮추기 전에 AI·IT의 정식 통과 글감을 쓴다
    assert llm.calls == 5  # 선별 2 + 작성·윤문·검증


def test_compose_post_raises_when_all_drafts_fail():
    bad = make_draft(action_items=[])
    llm, _ = fake_llm({
        'SelectionResult': [selection((0, 9))],
        'PostDraft': [bad, bad],
    })
    with pytest.raises(pipeline.PipelineError):
        pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)


def test_make_entry():
    llm, _ = fake_llm({
        'SelectionResult': [selection((0, 9))],
        'PostDraft': [make_draft()],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)
    entry = pipeline.make_entry(composed, {'id': '77', 'url': 'https://w/x.html', 'status': 'SCHEDULED'}, '2026-09-26')
    assert entry.post_id == '77' and entry.publish_date == '2026-09-26'
    assert entry.labels == ('AI활용',)
    assert entry.geeknews_url == 'https://news.hada.io/topic?id=34262'


def test_compose_post_includes_footer_link_by_default():
    llm, _ = fake_llm({
        'SelectionResult': [selection((0, 9))],
        'PostDraft': [make_draft()],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)
    assert 'https://link.inpock.co.kr/shoperwoody' in composed.html


def test_make_entry_drops_placeholder_url_for_drafts():
    llm, _ = fake_llm({
        'SelectionResult': [selection((0, 9))],
        'PostDraft': [make_draft()],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)
    draft = pipeline.make_entry(composed, {'id': '1', 'url': 'https://www.woodyway88.com/', 'status': 'DRAFT'}, 'd')
    assert draft.url == ''


MONEY_FEED = FEED.replace('https://news.hada.io/topic?id=', 'https://money.example.com/')


def money_get():
    """한국경제 경제 피드만 기사를 돌려주고, 기사 주소는 곧 원문이다 (direct 출처)."""
    base = fake_get()

    def get(url: str) -> str:
        if url == 'https://www.hankyung.com/feed/economy':
            return MONEY_FEED
        if url.startswith('https://money.example.com/'):
            return ARTICLE_HTML
        return base(url)
    return get


def test_compose_post_money_first_uses_money_prompt_and_persona():
    llm, client = fake_llm({
        'SelectionResult': [selection((0, 9))],
        'PostDraft': [make_draft(labels=['재테크'])],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, TECH_PAST, get=money_get(), is_alive=lambda u: True)
    assert composed.draft.labels == ['재테크']
    select, write = client.requests[0], client.requests[1]
    assert '생활경제·재테크 블로그 글감의 편집장' in select['instructions']
    assert '생활경제·재테크 필자' in write['instructions']
    assert renderer_note_in(composed.html)


def renderer_note_in(html: str) -> bool:
    from src import renderer
    return renderer.MONEY_NOTE in html


def test_compose_post_money_falls_back_to_tech_when_no_money_topic():
    llm, client = fake_llm({
        'SelectionResult': [selection((0, 3)), selection((0, 9))],  # 생활경제는 기준 미달 → AI·IT로
        'PostDraft': [make_draft()],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, TECH_PAST, get=money_get(), is_alive=lambda u: True)
    assert composed.draft.title == make_draft().title
    assert '생활경제·재테크' in client.requests[0]['instructions'] and '생활경제·재테크' not in client.requests[1]['instructions']


COPIED_SENTENCE = '원문 기사의 3번째 문단입니다. 직장인에게 도움이 되는 충분히 긴 내용이 여기에 들어갑니다.'


def copying_draft():
    filler = '회의록 정리는 많은 직장인이 매일 반복하는 일입니다. ' * 10
    sections = [
        make_draft().sections[0].model_copy(update={'body': f'{filler}\n\n{COPIED_SENTENCE}'}),
        make_draft().sections[1],
    ]
    return make_draft(sections=sections)


def test_duplicate_of_recent_post_skips_topic_without_rewriting():
    past = (make_entry(title=make_draft().title),)
    llm, client = fake_llm({
        'SelectionResult': [selection((0, 9), (1, 8))],
        'PostDraft': [make_draft(), make_draft()],
        'FactCheck': [OK],
    })
    with pytest.raises(pipeline.PipelineError):
        pipeline.compose_post(llm, MODELS, NOW, past, get=fake_get(), is_alive=lambda u: True)
    writes = [r for r in client.requests if r['text_format'].__name__ == 'PostDraft']
    assert len(writes) == 2  # 주제마다 1번만 쓰고 넘어간다 (다시 쓰지 않는다)


def test_rescue_publishes_after_removing_copied_sentence(capsys):
    llm, _ = fake_llm({
        'SelectionResult': [selection((0, 9))],
        'PostDraft': [copying_draft(), copying_draft()],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)
    assert COPIED_SENTENCE not in composed.html
    assert '[구제 발행]' in capsys.readouterr().out


def four_points():
    return make_draft(key_points=['첫째 핵심 내용입니다.', '둘째 핵심 내용입니다.', '셋째 핵심 내용입니다.', '넷째 핵심 내용입니다.'])


def test_rescue_removes_unsupported_claim_after_fact_check():
    claim = '회의는 30분 만에 끝났는데, 회의록 정리는 왜 한 시간이 걸릴까요?'
    bad = FactCheck(claims=[ClaimCheck(sentence='둘째 핵심 내용입니다.', kind='unsupported_fact', reason='근거 없음')])
    llm, _ = fake_llm({
        'SelectionResult': [selection((0, 9))],
        'PostDraft': [four_points(), four_points()],
        'FactCheck': [bad, OK],  # 한 번 쓰고 걸린 문장만 빼서 다시 검수하면 통과
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)
    assert '둘째 핵심 내용입니다.' not in composed.html
    assert claim in composed.html


def test_duplicate_title_on_second_attempt_still_rescues_first_draft():
    """1차 원고가 구제 가능했다면, 2차에서 제목이 겹쳐도 1차 원고를 버리지 않는다."""
    past = (make_entry(title='이전과 비슷한 제목입니다 회의록 AI 요약'),)
    similar = make_draft(title='이전과 비슷한 제목입니다 회의록 AI 요약', sections=copying_draft().sections)
    llm, _ = fake_llm({
        'SelectionResult': [selection((0, 9))],
        'PostDraft': [copying_draft(), similar],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, past, get=fake_get(), is_alive=lambda u: True)
    assert composed.draft.title == make_draft().title
    assert COPIED_SENTENCE not in composed.html


def test_short_news_only_lists_candidates_above_the_normal_bar():
    llm, _ = fake_llm({
        'SelectionResult': [selection((0, 9), (1, 8), (2, 5), (3, 4))],
        'PostDraft': [make_draft()],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)
    assert '한 줄 1' in composed.html and '한 줄 2' not in composed.html and '한 줄 3' not in composed.html
