"""발행 전 자동 품질 검수. 문제 목록이 비어 있으면 통과."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta, timezone

from src import kr_calendar
from src.history import HistoryEntry
from src.llm import LLMClient, load_prompt
from src.models import Article, ClaimCheck, FactCheck, PostDraft
from src.selector import TITLE_SIMILARITY_THRESHOLD, title_similarity

KST = timezone(timedelta(hours=9))

# 품질 하한 (2026-10-03 사용자 결정): 최근 글이 1,900~3,100자에 소제목(h2)이 없어 검색 유입이 줄었다.
# 본문 2,500자 이상, h2 섹션 3개 이상. 못 채우면 한 번 다시 쓰고, 그래도 모자라면 발행은 한다 (발행 건너뛰기 금지).
MIN_BODY_CHARS = 2500
MIN_SECTIONS = 3  # 섹션 소제목이 h2로 나간다
MAX_SECTIONS = 5
MAX_TITLE_CHARS = 40
MAX_META_CHARS = 150
BANNED_WORDS = ('충격', '경악', '필수', '모르면 손해', '대박', '역대급', '무조건', '100%',
                # 투자 권유로 읽히는 말 (생활경제·재테크 글)
                '지금 사세요', '매수하세요', '매도하세요', '수익 보장', '원금 보장', '추천주', '급등주', '리딩방')
# 썸네일 문구는 짧아서 낚시성 표현이 더 도드라진다
THUMBNAIL_BANNED_WORDS = (*BANNED_WORDS, '소름', '미쳤다', '실화냐', '레전드')
MAX_THUMBNAIL_CHARS = 14
MAX_HIGHLIGHT_CHARS = 6
# 모든 글이 "OO 3가지", "OO 3단계"로 똑같아 보이는 것을 막는다
FORMULAIC_TITLE = re.compile(r'\d+\s*(가지|단계)')
# 공백을 뺀 이 글자 수 이상이 원문과 똑같이 이어지면 옮겨 쓴 것으로 본다 (한국어 기사 표절 방지)
COPY_WINDOW = 30
# 도입부가 "알아봅니다/정리했습니다" 같은 안내문으로 흐르면 시선을 끌지 못한다
WEAK_HOOK_PHRASES = ('정리했습니다', '알아봅니다', '알아보겠습니다', '살펴봅니다', '살펴보겠습니다', '소개합니다')
# 그중 한글이 이 글자 수 이상일 때만 센다. 영어 제품명처럼 바꿔 쓸 수 없는 고유명사는 제외
MIN_HANGUL_IN_COPY = 15
_HANGUL_RE = re.compile(r'[가-힣]')
# 사실검증의 생각하는 양 (None = 모델 기본값). 9/28 'low'로 해 봤더니 후킹 문장·비유까지 '근거 없는 사실'로
# 잡아 원고 재작성(비싼 원고 모델 호출)이 늘어서 기본값으로 되돌렸다.
CHECK_EFFORT: str | None = None


@dataclass(frozen=True)
class QualityReport:
    issues: tuple[str, ...]
    duplicate_topic: bool = False  # 최근 글과 같은 소식. 다시 써도 소용없으니 주제를 바꾼다
    rescuable: bool = False  # 문제가 원문 옮겨 쓴 문장·근거 없는 문장뿐이라 그 문장만 빼면 발행할 수 있다
    unsupported: tuple[str, ...] = ()  # 사실검증이 근거 없다고 한 문장
    soft: tuple[str, ...] = ()  # 분량·소제목 하한 미달. 다시 쓰게 하되, 이것만 남으면 발행은 한다

    @property
    def passed(self) -> bool:
        return not self.issues

    @property
    def only_soft(self) -> bool:
        """분량·소제목 하한만 못 채웠고 나머지(사실검증 포함)는 통과했다."""
        return bool(self.issues) and set(self.issues) <= set(self.soft)


def body_text(draft: PostDraft) -> str:
    """분량 계산용 본문 평문."""
    parts = [
        draft.hook,
        *draft.key_points,
        *(f'{s.heading}\n{s.body}\n{s.tip}' for s in draft.sections),
        draft.why_it_matters,
        *draft.action_items,
    ]
    return '\n'.join(parts)


def fact_check_text(draft: PostDraft) -> str:
    """사실 검증 대상 본문. '내일 바로 써먹기'는 원래 원문에 없는 조언을 쓰는 자리라 제외합니다."""
    parts = [
        draft.title,
        draft.hook,
        *draft.key_points,
        *(f'{s.heading}\n{s.body}' for s in draft.sections),
        draft.why_it_matters,
        *(f'{g.term}: {g.meaning}' for g in draft.glossary),
        draft.thumbnail_text.replace('\n', ' '),
        ' '.join(x for x in (draft.answer_condition, draft.answer_value, draft.answer_detail) if x),
    ]
    return '\n'.join(parts)


def check_structure(draft: PostDraft) -> list[str]:
    issues = []
    if not 3 <= len(draft.key_points) <= 5:
        issues.append(f'핵심 내용 불릿은 3~5개여야 합니다 (현재 {len(draft.key_points)}개).')
    if len(draft.sections) > MAX_SECTIONS:
        issues.append(f'소제목(h2) 섹션은 {MAX_SECTIONS}개까지만 쓰세요 (현재 {len(draft.sections)}개).')
    if not 1 <= len(draft.action_items) <= 3:
        issues.append(f'내일 바로 써먹기는 1~3개여야 합니다 (현재 {len(draft.action_items)}개).')
    if not 1 <= len(draft.labels) <= 2:
        issues.append(f'라벨은 1~2개여야 합니다 (현재 {len(draft.labels)}개).')
    if any(not s.tip.strip() for s in draft.sections):
        issues.append('섹션마다 꿀팁(tip) 한 문장이 있어야 합니다.')
    if any(not g.term.strip() or not g.meaning.strip() for g in draft.glossary):
        issues.append('용어사전 항목에 용어와 쉬운 설명을 모두 적으세요.')
    if len(draft.title) > MAX_TITLE_CHARS:
        issues.append(f'제목이 {MAX_TITLE_CHARS}자를 넘습니다 (현재 {len(draft.title)}자).')
    if len(draft.meta_description) > MAX_META_CHARS:
        issues.append(f'메타 설명이 {MAX_META_CHARS}자를 넘습니다 (현재 {len(draft.meta_description)}자).')
    return issues


def _contains_ignoring_spaces(text: str, keyword: str) -> bool:
    return keyword.replace(' ', '') in text.replace(' ', '')


def check_seo(draft: PostDraft) -> list[str]:
    """검색 유입의 기본: 검색어가 제목과 메타 설명에 들어가 있어야 합니다."""
    keyword = draft.search_keyword.strip()
    issues = []
    if not keyword:
        return ['검색 키워드(search_keyword)가 비어 있습니다.']
    if not _contains_ignoring_spaces(draft.title, keyword):
        issues.append(f'제목에 검색 키워드 "{keyword}"를 그대로 넣으세요 (앞쪽일수록 좋습니다).')
    if not _contains_ignoring_spaces(draft.meta_description, keyword):
        issues.append(f'메타 설명에 검색 키워드 "{keyword}"를 넣으세요.')
    if FORMULAIC_TITLE.search(draft.title):
        issues.append('제목에 "N가지", "N단계" 같은 뻔한 틀을 쓰지 말고, 원문의 의외인 사실로 궁금증을 만드세요.')
    return issues


def copied_spans(text: str, source: str, window: int = COPY_WINDOW) -> list[tuple[int, int]]:
    """공백을 뺀 text에서 source와 window자 이상 똑같이 이어지는 구간의 (시작, 끝)을 찾습니다."""
    squashed, src = re.sub(r'\s+', '', text), re.sub(r'\s+', '', source)
    found, i = [], 0
    while i + window <= len(squashed):
        chunk = squashed[i:i + window]
        if chunk in src and len(_HANGUL_RE.findall(chunk)) >= MIN_HANGUL_IN_COPY:
            found.append((i, i + window))
            i += window
        else:
            i += 1
    return found


def copied_phrases(text: str, source: str, window: int = COPY_WINDOW) -> list[str]:
    """text 중 source와 공백 제외 window자 이상 똑같이 이어지는 부분을 찾습니다."""
    squashed = re.sub(r'\s+', '', text)
    return [squashed[a:b] for a, b in copied_spans(text, source, window)]


def check_copying(draft: PostDraft, article: Article) -> list[str]:
    found = copied_phrases(body_text(draft), article.text)
    if not found:
        return []
    examples = ' / '.join(f'"{c}"' for c in found[:2])
    return [f'원문 문장을 그대로 옮긴 부분이 {len(found)}곳 있습니다. 자신의 말로 바꿔 쓰세요: {examples}']


def check_hook(draft: PostDraft) -> list[str]:
    """뉴스 기사처럼 "회사·제품 이름이 ~를 발표했다"로 시작하거나 안내문으로 흐르는 도입부를 거릅니다."""
    hook = draft.hook.strip()
    issues = []
    keyword = draft.search_keyword.strip()
    if keyword and hook.replace(' ', '').startswith(keyword.replace(' ', '')):
        issues.append('도입부를 검색어(회사·제품 이름)로 시작하지 마세요. 독자가 겪는 장면이나 의외의 사실로 시작하세요.')
    found = [p for p in WEAK_HOOK_PHRASES if p in hook]
    if found:
        issues.append(f'도입부에 "{found[0]}" 같은 안내문을 쓰지 마세요. 긴장감이나 질문으로 끝내세요.')
    return issues


def body_length(draft: PostDraft) -> int:
    """본문 글자 수 (공백 포함, 연속 공백·줄바꿈은 한 칸으로)."""
    return len(re.sub(r'\s+', ' ', body_text(draft)).strip())


def check_length(draft: PostDraft) -> list[str]:
    length = body_length(draft)
    if length < MIN_BODY_CHARS:
        return [f'본문이 너무 짧습니다 (현재 {length}자, 최소 {MIN_BODY_CHARS}자). '
                '섹션마다 문단을 3~4개로 늘리고, 원문 사실·비교·구체적인 방법으로 채우세요 (같은 말 반복 금지).']
    return []


def check_headings(draft: PostDraft) -> list[str]:
    """검색 노출용 h2 소제목 섹션 수 하한."""
    if len(draft.sections) < MIN_SECTIONS:
        return [f'소제목(h2) 섹션이 {MIN_SECTIONS}개 이상이어야 합니다 (현재 {len(draft.sections)}개). '
                '독자가 검색할 만한 궁금증으로 섹션을 나눠 주세요.']
    return []


def check_floor(draft: PostDraft) -> list[str]:
    """분량·소제목 하한. 미달이면 다시 쓰게 하지만 이것 때문에 발행을 건너뛰지는 않는다."""
    return [*check_headings(draft), *check_length(draft)]


def check_banned_words(draft: PostDraft) -> list[str]:
    text = f'{draft.title}\n{body_text(draft)}'
    found = [w for w in BANNED_WORDS if w in text]
    return [f'과장/낚시성 표현을 빼세요: {", ".join(found)}'] if found else []


def check_thumbnail(draft: PostDraft) -> list[str]:
    """썸네일 문구: 2줄, 14자 이내, 강조 단어는 문구 안에 그대로, 낚시성 표현 금지. 비어 있으면 검사하지 않는다."""
    text, highlight = draft.thumbnail_text.strip(), draft.thumbnail_highlight.strip()
    if not text:
        return []
    issues = []
    length = len(text.replace('\n', ''))
    if length > MAX_THUMBNAIL_CHARS:
        issues.append(f'썸네일 문구는 줄바꿈 빼고 {MAX_THUMBNAIL_CHARS}자 이내로 줄이세요 (현재 {length}자).')
    if text.count('\n') > 1:
        issues.append('썸네일 문구는 두 줄까지만 쓰세요 (줄바꿈 1개).')
    if highlight and highlight not in text:
        issues.append(f'썸네일 강조 단어 "{highlight}"가 썸네일 문구 안에 그대로 있어야 합니다.')
    if len(highlight) > MAX_HIGHLIGHT_CHARS:
        issues.append(f'썸네일 강조 단어는 {MAX_HIGHLIGHT_CHARS}자 이내 한 단어로 고르세요 (현재 {len(highlight)}자).')
    found = [w for w in THUMBNAIL_BANNED_WORDS if w in text]
    if found:
        issues.append(f'썸네일 문구에서 과장/낚시성 표현을 빼세요: {", ".join(found)}')
    return issues


def check_duplicate_title(draft: PostDraft, history: tuple[HistoryEntry, ...]) -> list[str]:
    for e in history[-30:]:
        if title_similarity(e.title, draft.title) >= TITLE_SIMILARITY_THRESHOLD:
            return [f'최근 글과 제목이 비슷합니다: "{e.title}"']
    return []


def check_source(article: Article, is_alive: Callable[[str], bool]) -> list[str]:
    return [] if is_alive(article.source_url) else [f'출처 링크가 열리지 않습니다: {article.source_url}']


def article_date(article: Article) -> date:
    """원문 기사의 한국 날짜 (발행 시각이 UTC로 들어온다)."""
    return article.candidate.published.astimezone(KST).date()


def check_fact_claims(
    llm: LLMClient, model: str, draft: PostDraft, article: Article, day: date | None = None,
) -> list[ClaimCheck]:
    calendar = f'--- 달력 사실 (근거로 인정) ---\n{kr_calendar.calendar_facts(day)}\n\n' if day is not None else ''
    # 원문의 '올해·지난해·내년'을 숫자 연도로 바꾼 문장을 근거 없음으로 잡지 않게 기사 날짜를 함께 준다 (2026-10-21)
    dated = f'원문 기사 날짜: {article_date(article).isoformat()}\n\n'
    user_input = f'{calendar}{dated}--- 원문 ---\n{article.text}\n\n--- 작성된 글 ---\n{fact_check_text(draft)}'
    result = llm.parse(model, load_prompt('factcheck'), user_input, FactCheck, effort=CHECK_EFFORT, stage='factcheck')
    return [c for c in result.claims if c.kind == 'unsupported_fact']


def check_facts(llm: LLMClient, model: str, draft: PostDraft, article: Article) -> list[str]:
    return [
        f'원문에 근거 없는 사실: "{c.sentence}" ({c.reason})'
        for c in check_fact_claims(llm, model, draft, article)
    ]


def check_calendar(draft: PostDraft, day: date | None) -> list[tuple[str, str]]:
    """실제 달력과 안 맞는 시점 표현 ("내일 출근"인데 내일이 공휴일 등)이 든 문장과 이유. 제목·썸네일 포함."""
    if day is None:
        return []
    fields = [draft.title, draft.thumbnail_text.replace('\n', ' '), draft.hook, *draft.key_points,
              *(f'{s.heading}\n{s.body}\n{s.tip}' for s in draft.sections), draft.why_it_matters, *draft.action_items]
    found: list[tuple[str, str]] = []
    for field in fields:
        for item in kr_calendar.timing_problems(field, day):
            if item not in found:
                found.append(item)
    return found


def run_quality_gate(
    llm: LLMClient,
    fast_model: str,
    draft: PostDraft,
    article: Article,
    history: tuple[HistoryEntry, ...],
    is_alive: Callable[[str], bool],
    day: date | None = None,
) -> QualityReport:
    """값싼 규칙 검사를 먼저 하고, 모두 통과했을 때만 LLM 사실 검증을 호출합니다.

    분량·소제목 하한(soft)은 사실 검증을 막지 않는다. 하한만 남은 원고는 발행할 수 있어야 하기 때문이다.
    """
    soft = tuple(check_floor(draft))
    copying = check_copying(draft, article)
    duplicate = check_duplicate_title(draft, history)
    other = [
        *check_structure(draft),
        *check_seo(draft),
        *check_hook(draft),
        *check_banned_words(draft),
        *check_thumbnail(draft),
        *check_source(article, is_alive),
    ]
    timing = check_calendar(draft, day)  # 달력과 안 맞는 시점 표현: 다시 쓰게 하고, 끝내 못 고치면 그 문장만 뺀다
    timing_issues = [f'달력과 맞지 않는 표현: "{s}" ({why}) 시점 표현을 고치거나 빼세요.' for s, why in timing]
    timing_sentences = tuple(s for s, _ in timing)
    hard = [*other, *copying, *duplicate]
    if hard:
        return QualityReport((*hard, *timing_issues, *soft), duplicate_topic=bool(duplicate),
                             rescuable=(bool(copying) or bool(timing)) and not other and not duplicate,
                             unsupported=timing_sentences, soft=soft)
    claims = check_fact_claims(llm, fast_model, draft, article, day)
    return QualityReport(
        (*(f'원문에 근거 없는 사실: "{c.sentence}" ({c.reason})' for c in claims), *timing_issues, *soft),
        rescuable=bool(claims) or bool(timing),
        unsupported=(*(c.sentence for c in claims), *timing_sentences),
        soft=soft,
    )
