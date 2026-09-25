import pytest

from src import main as pipeline
from src.models import CandidateScore, FactCheck, SelectionResult
from tests.helpers import FIXTURES, NOW, fake_llm, make_draft

FEED = (FIXTURES / 'geeknews_feed.xml').read_text(encoding='utf-8')
MODELS = pipeline.Models(writer='writer-model', fast='fast-model')
ARTICLE_HTML = '<html><body><article>' + ''.join(
    f'<p>원문 기사의 {i}번째 문단입니다. 직장인에게 도움이 되는 충분히 긴 내용이 여기에 들어갑니다.</p>' for i in range(30)
) + '</article></body></html>'


def fake_get(broken_topics: set[str] = frozenset()):
    def get(url: str) -> str:
        if url == 'https://news.hada.io/rss/news':
            return FEED
        if url.startswith('https://news.hada.io/topic?id='):
            topic_id = url.split('=')[1]
            if topic_id in broken_topics:
                return '<p>no title link</p>'
            return f"<a href='https://example.com/{topic_id}' class='topic-title-link'>t</a>"
        if url.startswith('https://example.com/'):
            return ARTICLE_HTML
        raise AssertionError(f'unexpected url {url}')
    return get


def selection(*indexes_scores):
    return SelectionResult(scores=[
        CandidateScore(index=i, score=s, reason='r', one_line=f'한 줄 {i}') for i, s in indexes_scores
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
    assert [r['model'] for r in client.requests] == ['fast-model', 'writer-model', 'fast-model']
    assert llm.calls == 3


def test_compose_post_rewrites_once_with_feedback():
    llm, client = fake_llm({
        'SelectionResult': [selection((0, 9))],
        'PostDraft': [make_draft(action_items=[]), make_draft()],
        'FactCheck': [OK],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)
    assert composed.draft.action_items
    assert '검수에서 아래 이유로 탈락' in client.requests[2]['input']


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
    llm, _ = fake_llm({'SelectionResult': [selection((0, 3), (1, 5))]})
    with pytest.raises(pipeline.PipelineError):
        pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)


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
