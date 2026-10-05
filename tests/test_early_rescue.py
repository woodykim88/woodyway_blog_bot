"""검수에 걸린 게 문장 몇 개뿐이면 원고를 다시 쓰지 않고 그 문장만 빼고 발행한다 (원고 모델 재호출 방지)."""
from dataclasses import replace

from src import main as pipeline
from src import quality
from src.models import ClaimCheck, FactCheck, Section
from tests.helpers import fake_llm, make_article, make_draft
from tests.test_pipeline import COPIED_SENTENCE, MODELS, OK

ALIVE = lambda url: True  # noqa: E731
FILLER = '회의록 정리는 많은 직장인이 매일 반복하는 일입니다. '


def article_with_copied_sentence():
    article = make_article()
    return replace(article, text=article.text + COPIED_SENTENCE)


def full_copying_draft(extra_filler: int = 15):
    """분량·소제목 하한은 채웠고, 한 문단에 원문 문장이 그대로 들어 있는 원고."""
    sections = [
        Section(heading=f'궁금증 {i}', body=f'{FILLER * extra_filler}\n\n{FILLER * extra_filler}', tip='바로 해 보세요.')
        for i in range(3)
    ]
    sections[0] = sections[0].model_copy(update={'body': f'{sections[0].body}\n\n{COPIED_SENTENCE}'})
    return make_draft(sections=sections)


def writes(client):
    return [r for r in client.requests if r['text_format'].__name__ == 'PostDraft']


def test_copied_sentence_is_removed_without_a_second_write(capsys):
    draft = full_copying_draft()
    assert not quality.check_floor(draft)
    llm, client = fake_llm({'PostDraft': [draft, draft], 'FactCheck': [OK]})
    fixed = pipeline.draft_article(llm, MODELS, article_with_copied_sentence(), '테마', (), ALIVE)
    assert fixed is not None
    assert COPIED_SENTENCE not in ' '.join(s.body for s in fixed.sections)
    assert len(writes(client)) == 1
    assert '[구제 발행]' in capsys.readouterr().out


def test_unsupported_claim_is_removed_without_a_second_write():
    draft = make_draft(key_points=['첫째 핵심 내용입니다.', '둘째 핵심 내용입니다.', '셋째 핵심 내용입니다.', '넷째 핵심 내용입니다.'])
    bad = FactCheck(claims=[ClaimCheck(sentence='둘째 핵심 내용입니다.', kind='unsupported_fact', reason='근거 없음')])
    llm, client = fake_llm({'PostDraft': [draft, draft], 'FactCheck': [bad, OK]})
    fixed = pipeline.draft_article(llm, MODELS, make_article(), '테마', (), ALIVE)
    assert '둘째 핵심 내용입니다.' not in fixed.key_points
    assert len(writes(client)) == 1


def test_short_draft_with_a_copied_sentence_is_still_rewritten():
    """분량 하한도 못 채웠다면 문장만 빼서는 안 되므로 예전처럼 다시 쓴다."""
    short = make_draft(sections=full_copying_draft().sections[:2])
    assert quality.check_floor(short)
    llm, client = fake_llm({'PostDraft': [short, make_draft()], 'FactCheck': [OK, OK]})
    pipeline.draft_article(llm, MODELS, article_with_copied_sentence(), '테마', (), ALIVE)
    assert len(writes(client)) == 2


def test_removal_that_pushes_the_body_under_the_floor_falls_back_to_rewriting():
    # 원문 문장이 있을 땐 하한(2,500자)을 겨우 넘고, 빼면 밑으로 내려가는 원고: 다시 쓴다
    base = full_copying_draft(12)
    pad = quality.MIN_BODY_CHARS + 10 - quality.body_length(base)
    draft = base.model_copy(update={'why_it_matters': base.why_it_matters + '가' * pad})
    assert not quality.check_floor(draft)
    llm, client = fake_llm({'PostDraft': [draft, make_draft()], 'FactCheck': [OK, OK]})
    fixed = pipeline.draft_article(llm, MODELS, article_with_copied_sentence(), '테마', (), ALIVE)
    assert fixed is not None and len(writes(client)) == 2


def article_with(passage: str):
    article = make_article()
    return replace(article, text=article.text + passage)


def test_early_rescue_that_would_cut_a_lot_of_text_rewrites_instead():
    passage = ' '.join(f'원문 기사의 {i}번째 문단입니다. 직장인에게 도움이 되는 충분히 긴 내용이 여기에 들어갑니다.' for i in range(12))
    base = full_copying_draft()
    sections = [base.sections[0].model_copy(update={'body': f'{base.sections[0].body}\n\n{passage}'}), *base.sections[1:]]
    llm, client = fake_llm({'PostDraft': [make_draft(sections=sections), make_draft()], 'FactCheck': [OK, OK]})
    fixed = pipeline.draft_article(llm, MODELS, article_with(passage), '테마', (), ALIVE)
    assert fixed is not None and len(writes(client)) == 2  # 600자 넘게 잘려 나가면 살리지 않고 다시 쓴다


def test_last_attempt_does_not_run_the_same_rescue_twice():
    from tests.test_quality_floor import short_draft
    llm, client = fake_llm({'PostDraft': [short_draft(), full_copying_draft()], 'FactCheck': [OK, OK, OK]})
    fixed = pipeline.draft_article(llm, MODELS, article_with_copied_sentence(), '테마', (), ALIVE)
    assert fixed is not None and COPIED_SENTENCE not in ' '.join(s.body for s in fixed.sections)
    checks = [r for r in client.requests if r['text_format'].__name__ == 'FactCheck']
    # 시도1 검수 + 마지막 구제 검수 1번뿐. (원문 옮김은 규칙 검사에서 걸려 사실검증 호출이 없다.) 시도2에서 구제를 미리 하면 3번이 된다
    assert len(checks) == 2


def test_title_is_fitted_to_the_target_query_not_the_writers_keyword():
    from tests.test_fieldfix import LONG_TITLE, fitted
    draft = make_draft(title=LONG_TITLE, search_keyword='엉뚱한 검색어')
    llm, client = fake_llm({
        'PostDraft': [draft],
        'FittedFields': [fitted(draft, title='회의록 AI 요약, 정리가 1분이면 끝나는 이유')],
        'FactCheck': [OK],
    })
    fixed = pipeline.draft_article(llm, MODELS, make_article(), '테마', (), ALIVE, target_query='회의록 AI 요약')
    assert fixed is not None and fixed.title == '회의록 AI 요약, 정리가 1분이면 끝나는 이유'
    assert len(writes(client)) == 1
