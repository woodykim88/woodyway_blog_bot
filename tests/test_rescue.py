from src import rescue
from src.models import Article, GlossaryItem, Section
from tests.helpers import make_article, make_candidate, make_draft

COPIED = '민자고속도로 요금소로 나갔다가 다시 들어오는 경우에는 기본요금이 다시 부과됩니다.'
SAFE = '이 내용은 내 통행료에 바로 영향을 줍니다.'


def article_with(sentence: str) -> Article:
    return Article(candidate=make_candidate(), source_url='https://example.com/a', text=f'기사 앞부분입니다. {sentence} 기사 뒷부분입니다.')


def draft_with_body(body: str, **overrides):
    filler = '회의록 정리는 많은 직장인이 매일 반복하는 일입니다. ' * 10
    sections = [Section(heading='배경', body=body, tip='꿀팁 한 줄입니다.'), Section(heading='방식', body=filler, tip='꿀팁 둘째입니다.')]
    return make_draft(sections=sections, **overrides)


def test_removes_only_the_copied_sentence():
    draft = draft_with_body(f'{SAFE} {COPIED} 그래서 확인이 필요합니다.')
    fixed = rescue.remove_flagged(draft, article_with(COPIED), ())
    body = fixed.sections[0].body
    assert COPIED not in body
    assert SAFE in body and '그래서 확인이 필요합니다.' in body


def test_removes_sentence_that_starts_before_the_copied_span():
    """복사 구간이 앞 문장 끝에서 시작해도 겹친 문장만 뺀다."""
    draft = draft_with_body(f'{SAFE} {COPIED}')
    fixed = rescue.remove_flagged(draft, article_with(COPIED[3:]), ())
    assert SAFE in fixed.sections[0].body


def test_removes_unsupported_claim_sentence_from_hook_and_bullets():
    claim = '이 제도는 내년 1월부터 전면 시행됩니다.'
    draft = make_draft(
        hook=f'첫 문장입니다.\n\n{claim}',
        key_points=['첫째 핵심입니다.', claim, '셋째 핵심입니다.', '넷째 핵심입니다.'],
    )
    fixed = rescue.remove_flagged(draft, make_article(), (claim,))
    assert claim not in fixed.hook
    assert fixed.hook == '첫 문장입니다.'
    assert fixed.key_points == ['첫째 핵심입니다.', '셋째 핵심입니다.', '넷째 핵심입니다.']


def test_drops_glossary_item_flagged_as_unsupported():
    draft = make_draft(glossary=[GlossaryItem(term='STT', meaning='말소리를 글자로 바꿔 줍니다.'), GlossaryItem(term='LLM', meaning='큰 언어 모델입니다.')])
    fixed = rescue.remove_flagged(draft, make_article(), ('STT: 말소리를 글자로 바꿔 줍니다.',))
    assert [g.term for g in fixed.glossary] == ['LLM']


def test_gives_up_when_claim_is_in_title():
    draft = make_draft(title='근거 없는 주장이 제목에 있습니다')
    assert rescue.remove_flagged(draft, make_article(), ('근거 없는 주장이 제목에 있습니다',)) is None


def test_gives_up_when_claim_cannot_be_located():
    assert rescue.remove_flagged(make_draft(), make_article(), ('본문 어디에도 없는 문장입니다 정말로',)) is None


def test_gives_up_when_hook_would_become_empty():
    claim = '도입부 전체가 근거 없는 한 문장입니다.'
    assert rescue.remove_flagged(make_draft(hook=claim), make_article(), (claim,)) is None


def test_returns_none_when_nothing_to_remove():
    assert rescue.remove_flagged(make_draft(), make_article(), ()) is None


def test_gives_up_when_any_claim_is_not_located_even_if_another_is_removed():
    hook_claim = '도입부의 근거 없는 주장입니다.'
    draft = make_draft(hook=f'첫 문장입니다.\n\n{hook_claim}')
    assert rescue.remove_flagged(draft, make_article(), (hook_claim, '어디에도 없는 바꿔 쓴 문장입니다 정말')) is None


def test_gives_up_when_claim_is_in_a_section_heading():
    heading = '근거 없는 소제목입니다'
    sections = [make_draft().sections[0].model_copy(update={'heading': heading}), make_draft().sections[1]]
    hook_claim = '도입부의 근거 없는 주장입니다.'
    draft = make_draft(sections=sections, hook=f'첫 문장입니다.\n\n{hook_claim}')
    assert rescue.remove_flagged(draft, make_article(), (hook_claim, heading)) is None


def test_claim_left_in_why_it_matters_blocks_rescue_when_only_hook_is_cleanable():
    why = '이 문장은 왜 중요한지에 남아 있는 근거 없는 설명입니다.'
    draft = make_draft(why_it_matters=why)
    fixed = rescue.remove_flagged(draft, make_article(), (why,))
    assert fixed is None  # why_it_matters가 비면 빼도 글이 성립하지 않는다
