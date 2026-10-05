"""글자 수만 넘친 제목·메타 설명·썸네일은 원고를 다시 쓰지 않고 그 칸만 값싼 모델로 고친다."""
from src import fieldfix, quality
from src.llm import LLMError
from src.models import FactCheck, FittedFields
from tests.helpers import fake_llm, make_draft

LONG_THUMB = '회의록 정리 이제\n1분이면 끝나요요'  # 줄바꿈 빼고 15자 이상
LONG_TITLE = '회의록 AI 요약, 30분 걸리던 정리가 1분이면 끝나는 이유를 한 번에 알려드립니다 정말로'


def fitted(draft, **changes):
    values = {'title': draft.title, 'meta_description': draft.meta_description,
              'thumbnail_text': draft.thumbnail_text, 'thumbnail_highlight': draft.thumbnail_highlight, **changes}
    return FittedFields(**values)


def test_no_call_when_every_field_fits():
    llm, client = fake_llm({})
    draft = make_draft()
    assert fieldfix.fit_fields(llm, 'fast', draft) == draft
    assert client.requests == [] and llm.calls == 0


def test_long_thumbnail_is_shortened_with_one_cheap_call_and_other_fields_stay():
    draft = make_draft(thumbnail_text=LONG_THUMB, thumbnail_highlight='1분이면')
    assert quality.check_thumbnail(draft)
    llm, client = fake_llm({'FittedFields': [fitted(
        draft, thumbnail_text='회의록 정리\n1분이면 끝', thumbnail_highlight='1분',
        title='모델이 멋대로 바꾼 제목입니다 회의록 AI 요약')]})
    fixed = fieldfix.fit_fields(llm, 'fast', draft)
    assert fixed.thumbnail_text == '회의록 정리\n1분이면 끝' and fixed.thumbnail_highlight == '1분'
    assert fixed.title == draft.title  # 고치라고 한 칸만 바뀐다
    assert not quality.check_thumbnail(fixed)
    assert [r['model'] for r in client.requests] == ['fast']


def test_long_title_is_shortened_but_must_keep_the_search_keyword():
    draft = make_draft(title=LONG_TITLE)
    assert len(draft.title) > quality.MAX_TITLE_CHARS
    llm, _ = fake_llm({'FittedFields': [fitted(draft, title='회의록 AI 요약, 정리가 1분이면 끝나는 이유')]})
    fixed = fieldfix.fit_fields(llm, 'fast', draft)
    assert fixed.title == '회의록 AI 요약, 정리가 1분이면 끝나는 이유'


def test_fix_that_drops_keyword_or_is_still_too_long_is_ignored():
    draft = make_draft(title=LONG_TITLE)
    for bad in ('정리가 1분이면 끝나는 이유', LONG_TITLE):
        llm, _ = fake_llm({'FittedFields': [fitted(draft, title=bad)]})
        assert fieldfix.fit_fields(llm, 'fast', draft).title == LONG_TITLE


def test_long_meta_description_is_shortened_keeping_keyword():
    draft = make_draft(meta_description='회의록 AI 요약 ' + '정리 시간을 줄이는 방법 ' * 20)
    llm, _ = fake_llm({'FittedFields': [fitted(draft, meta_description='회의록 AI 요약으로 정리 시간을 줄이는 방법')]})
    assert fieldfix.fit_fields(llm, 'fast', draft).meta_description == '회의록 AI 요약으로 정리 시간을 줄이는 방법'


def test_llm_failure_keeps_the_draft_so_the_quality_gate_still_decides():
    draft = make_draft(thumbnail_text=LONG_THUMB)
    llm, _ = fake_llm({}, max_calls=0)  # 호출 상한 초과 = LLMError
    assert fieldfix.fit_fields(llm, 'fast', draft) == draft
    llm2, _ = fake_llm({'FittedFields': [None]})
    assert fieldfix.fit_fields(llm2, 'fast', draft) == draft


def test_pipeline_does_not_rewrite_the_article_for_a_long_thumbnail():
    from tests.test_pipeline import MODELS, NOW, fake_get, pipeline, selection
    draft = make_draft(thumbnail_text=LONG_THUMB, thumbnail_highlight='1분이면')
    llm, client = fake_llm({
        'SelectionResult': [selection((0, 9))],
        'PostDraft': [draft, make_draft()],
        'FittedFields': [fitted(draft, thumbnail_text='회의록 정리\n1분이면 끝', thumbnail_highlight='1분')],
        'FactCheck': [FactCheck(claims=[])],
    })
    composed = pipeline.compose_post(llm, MODELS, NOW, (), get=fake_get(), is_alive=lambda u: True)
    writes = [r for r in client.requests if r['text_format'].__name__ == 'PostDraft']
    assert len(writes) == 1  # 원고 모델은 한 번만 쓴다
    assert composed.draft.thumbnail_text == '회의록 정리\n1분이면 끝'


def test_any_api_error_keeps_the_draft_instead_of_failing_the_publish():
    from src.llm import LLMClient

    class Boom:
        def parse(self, *a, **k):
            raise RuntimeError('rate limited')

    draft = make_draft(thumbnail_text=LONG_THUMB)
    assert fieldfix.fit_fields(LLMClient(Boom()), 'fast', draft) == draft


def test_fix_using_a_banned_word_is_ignored():
    draft = make_draft(title=LONG_TITLE)
    llm, _ = fake_llm({'FittedFields': [fitted(draft, title='회의록 AI 요약, 충격적인 1분 정리')]})
    assert fieldfix.fit_fields(llm, 'fast', draft).title == LONG_TITLE
