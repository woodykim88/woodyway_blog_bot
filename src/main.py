"""블로그 글 생성·발행 파이프라인.

수집 → 선별 → 원문 → 작성 → 검수 → 대표 이미지 → 렌더링 → 발행 → 이력/실행 기록.

실행 모드:
    auto      PUBLISH_TIMES 중 앞으로 6시간 안에 돌아오는 슬롯을 준비해 예약 (GitHub Actions 정기 실행)
    schedule  아직 준비 안 된 가장 가까운 슬롯(또는 --at 시각)에 1편 예약
    draft     1편을 초안으로만 저장
    now       1편을 즉시 공개 발행
    preview   Blogger에 올리지 않고 output/preview.html만 생성

사용법:
    python -m src.main --mode preview
    python -m src.main --mode auto
"""
from __future__ import annotations

import argparse
import html
import os
import sys
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from googleapiclient.errors import HttpError

import auto_blogger
from src import (
    collector,
    disclosure,
    fetcher,
    fieldfix,
    guide_topic,
    history,
    howto_topic,
    image,
    infographic,
    notifier,
    providers,
    quality,
    renderer,
    rescue,
    runlog,
    schedule,
    selector,
    skill_topic,
    skills,
    thumbnail,
    tools,
    topics,
    trends,
    polish,
    writer,
)
from src.http_client import HttpGet, http_get, url_is_alive
from src.llm import LLMClient, LLMError, load_prompt
from src.models import Article, PostDraft, RankedCandidate, ShortNews
from src import usage


def _image_errors() -> tuple[type[Exception], ...]:
    """이미지 단계에서 잡을 외부 라이브러리 오류 (설치된 것만)."""
    errors: list[type[Exception]] = [OSError, ValueError]
    try:
        from botocore.exceptions import BotoCoreError, ClientError
        errors += [BotoCoreError, ClientError]
    except ImportError:  # pragma: no cover
        pass
    try:
        from openai import OpenAIError
        errors.append(OpenAIError)
    except ImportError:  # pragma: no cover
        pass
    try:
        from google.genai.errors import APIError
        errors.append(APIError)
    except ImportError:  # pragma: no cover
        pass
    return tuple(errors)


IMAGE_ERRORS = _image_errors()
MODES = ('auto', 'schedule', 'draft', 'now', 'preview')
MAX_TOPIC_ATTEMPTS = 3
RECENT_TITLES_FOR_SELECTOR = 12  # 선별 모델에 알려 줄 최근 발행 글 수 (같은 소식 재발행 방지)
DEFAULT_SKILL_TIME = '12:30'  # 이 시각 칸은 Claude Code 스킬 글 (루이 채널과 원 소스 멀티 유즈)
MAX_WRITE_ATTEMPTS = 2
MAX_EARLY_RESCUE_LOSS_CHARS = 400  # 구제로 이만큼(공백 포함 글자)보다 많이 잘려 나가면 살리지 않고 다시 쓴다
MAX_GUIDE_ATTEMPTS = 2  # 12:30 칸 예비 사용법 글감을 이만큼까지 써 본다 (AI 호출 상한)
# 이 시각 칸은 검색형 생활 가이드 글 (2026-10-03 사용자 결정, 검색 유입 목적).
# 2026-10-10: 08:00 뉴스 글은 28일 동안 검색 노출이 거의 없어 08:00도 생활 가이드로. 되돌리려면 HOWTO_TIME=20:00
DEFAULT_HOWTO_TIME = '08:00,20:00'
MAX_HOWTO_ATTEMPTS = 2  # 생활 가이드 글감을 이만큼까지 써 보고, 못 쓰면 뉴스로 발행 (발행 건너뛰기 금지)
SHORT_NEWS_COUNT = 3
DEFAULT_FOOTER_URL = 'https://link.inpock.co.kr/shoperwoody'
PREVIEW_PATH = Path(__file__).resolve().parent.parent / 'output' / 'preview.html'
PREVIEW_IMAGE_NAME = 'hero.webp'


class PipelineError(Exception):
    """발행할 글을 만들지 못했을 때 발생합니다."""


@dataclass(frozen=True)
class Models:
    writer: str
    fast: str


@dataclass(frozen=True)
class Composed:
    article: Article
    draft: PostDraft
    short_news: tuple[ShortNews, ...] = ()
    related: tuple[history.HistoryEntry, ...] = ()
    footer_url: str | None = None
    hero_image_url: str | None = None
    body_images: tuple[tuple[str, str], ...] = ()  # (이미지 주소, 대체 텍스트) 본문 중간 그림
    infographics: tuple[tuple[str, str, str], ...] = ()  # (종류 summary|checklist, 이미지 주소, 대체 텍스트)
    tool: tools.Tool | None = None
    category: str = topics.TECH
    skill: skills.Skill | None = None
    as_of: str = ''  # 생활 가이드 글의 기준 시점 (예: 2026년 10월 3일)

    @property
    def html(self) -> str:
        return renderer.render_post(
            self.draft,
            self.article,
            short_news=self.short_news,
            related=self.related,
            hero_image_url=self.hero_image_url,
            body_images=self.body_images,
            infographics=self.infographics,
            footer_url=self.footer_url,
            tool=self.tool,
            skill=self.skill,
            as_of=self.as_of,
        )


@dataclass(frozen=True)
class Target:
    """이번 실행에서 만들 글 1편의 목표."""

    slot: str                   # 슬롯 키 또는 manual-/preview 키
    publish_at: datetime | None  # schedule일 때만
    action: str                 # schedule / draft / now / preview

    @property
    def publish_date(self) -> str:
        if self.publish_at is not None:
            return self.publish_at.astimezone(schedule.KST).date().isoformat()
        return datetime.now(schedule.KST).date().isoformat()


# --- 글 만들기 ---

def _rescue_before_rewrite(
    llm: LLMClient, models: 'Models', draft: PostDraft, article: Article, report: quality.QualityReport,
    past: tuple[history.HistoryEntry, ...], is_alive: Callable[[str], bool], day: date | None,
) -> PostDraft | None:
    """걸린 문장이 몇 개뿐이면 원고를 다시 쓰지 않고(원고 모델 2회 호출) 그 문장만 빼서 살린다 (값싼 모델 검수 1회).

    분량·소제목 하한도 못 채운 원고, 빼고 나서 하한 밑으로 내려가는 원고, 글이 눈에 띄게 줄어드는 원고
    (후킹·비유 문장까지 잘려 나가는 경우)는 문장만 빼서는 안 되니 None (다시 쓴다).
    """
    if report.soft or report.duplicate_topic:
        return None
    rescued = rescue.rescue(llm, models.fast, draft, article, report.unsupported, past, is_alive, day)
    if rescued is None or quality.check_floor(rescued):
        return None
    if quality.body_length(draft) - quality.body_length(rescued) > MAX_EARLY_RESCUE_LOSS_CHARS:
        return None
    print(f'[구제 발행] 원고를 다시 쓰지 않고 검수에 걸린 문장만 빼고 발행합니다: {rescued.title}')
    return rescued


def draft_article(
    llm: LLMClient,
    models: Models,
    article: Article,
    theme: str,
    past: tuple[history.HistoryEntry, ...],
    is_alive: Callable[[str], bool],
    category: str = topics.TECH,
    target_query: str = '',
    day: date | None = None,
    kind_rules: str = '',
) -> PostDraft | None:
    """글을 쓰고 사람이 쓴 글처럼 윤문한 뒤 검수합니다. 탈락하면 검수 의견을 반영해 한 번 더 씁니다.

    target_query(생활 가이드 글의 목표 검색어)를 주면 검색어를 그 글자 그대로 고정해, 제목·메타 설명에
    들어갔는지 검수(check_seo)가 확인하게 한다.

    day(발행일)를 주면 공휴일·대체공휴일 달력 사실을 프롬프트에 넣고, 달력과 안 맞는 시점 표현은 검수에서 걸린다
    (끝내 못 고치면 그 문장만 빼고 발행).

    분량·소제목 하한(quality.check_floor)만 못 채운 원고는 버리지 않는다: 다시 써 보고, 그래도 모자라면
    구제 발행이 안 될 때 그 원고로 발행한다 (발행 건너뛰기 금지, 운영자 지시).
    """
    feedback: tuple[str, ...] = ()
    salvage: tuple[PostDraft, quality.QualityReport] | None = None
    short: PostDraft | None = None  # 하한만 못 채우고 나머지 검수는 통과한 원고
    for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
        written = polish.polish_draft(llm, models.writer, writer.write_post(
            llm, models.writer, article, theme, feedback, category, target_query, day, kind_rules))
        draft = disclosure.strip_ai_disclosure(written)  # AI 작성 고지는 절대 싣지 않는다 (운영자 지시)
        if target_query:
            draft = draft.model_copy(update={'search_keyword': target_query})
        draft = fieldfix.fit_fields(llm, models.fast, draft)  # 제목·썸네일 글자 수만 넘쳤으면 그 칸만 고친다 (원고 재작성 방지)
        report = quality.run_quality_gate(llm, models.fast, draft, article, past, is_alive, day)
        if report.passed:
            print(f'[검수 통과] {draft.title} (시도 {attempt})')
            return draft
        print(f'[검수 탈락] 시도 {attempt}: ' + ' / '.join(report.issues))
        if report.duplicate_topic:
            if salvage is None and short is None:
                print('[건너뜀] 최근에 발행한 소식과 같아 다른 주제로 넘어갑니다')
                return None  # 다시 써도 같은 소식이라 원고 모델 호출만 낭비된다
            break  # 앞선 원고를 구제할 수 있으니 버리지 않는다
        if report.only_soft:
            short = draft
        elif report.rescuable:
            # 마지막 시도는 루프 뒤의 구제 발행이 같은 일을 하므로 여기서 또 하지 않는다 (호출 낭비)
            early = None if attempt == MAX_WRITE_ATTEMPTS else _rescue_before_rewrite(
                llm, models, draft, article, report, past, is_alive, day)
            if early is not None:
                return early
            salvage = (draft, report)
        feedback = report.issues
    rescued = None
    if salvage is not None:
        # 원고 때문에 발행을 건너뛰지 않는다 (운영자 지시): 걸린 문장만 빼고 발행한다
        draft, report = salvage
        rescued = rescue.rescue(llm, models.fast, draft, article, report.unsupported, past, is_alive, day)
        if rescued is not None:
            print(f'[구제 발행] 검수에 걸린 문장만 빼고 발행합니다: {rescued.title}')
    if rescued is None and short is not None:
        print(f'[분량 미달 발행] 다시 써도 분량·소제목 하한을 못 채웠지만 나머지 검수는 통과해 발행합니다: {short.title}')
        return short
    return rescued


def pick_short_news(ranked: Sequence[RankedCandidate], used_url: str) -> list[ShortNews]:
    others = [r for r in ranked if r.candidate.link != used_url]
    return [ShortNews(r.candidate.title, r.one_line, r.candidate.link) for r in others[:SHORT_NEWS_COUNT]]


def is_skill_slot(publish_at: datetime | None, skill_time: schedule.Time | None) -> bool:
    """예약 발행 칸이 그 시각(스킬 글 12:30, 생활 가이드 20:00)이면 True. 수동 발행(시각 없음)은 뉴스 글."""
    if publish_at is None or skill_time is None:
        return False
    local = publish_at.astimezone(schedule.KST)
    return (local.hour, local.minute) == skill_time


def force_first_label(draft: PostDraft, label: str) -> PostDraft:
    """12:30 칸 글은 첫 라벨을 고정한다 (분야 판별·썸네일 라벨·반반 계산 제외의 기준)."""
    rest = [x for x in draft.labels if x not in topics.FIXED_LABELS]
    return draft.model_copy(update={'labels': [label, *rest][:2]})


def force_skill_labels(draft: PostDraft) -> PostDraft:
    """스킬 글은 첫 라벨을 항상 AI스킬로."""
    return force_first_label(draft, topics.SKILL_LABEL)


def compose_skill_post(
    llm: LLMClient,
    models: Models,
    now: datetime,
    past: tuple[history.HistoryEntry, ...],
    get: HttpGet = http_get,
    is_alive: Callable[[str], bool] = url_is_alive,
    footer_url: str | None = DEFAULT_FOOTER_URL,
    day: date | None = None,
) -> Composed | None:
    """skills.sh에서 스킬 하나를 골라 글을 씁니다. 고를 스킬이 없거나 검수를 못 넘으면 None (AI 도구 사용법 글로 대신)."""
    try:
        skill = skill_topic.pick_skill(
            lambda: skills.fetch_candidates(get), lambda ref: skills.fetch_detail(get, ref), past,
            choose=lambda found: skill_topic.choose_popular(llm, models.fast, found),
            prefilter=lambda refs: skill_topic.rank_by_name(llm, models.fast, refs),
        )
    except skills.SourceError as e:
        print(f'[스킬] {e}')
        return None
    if skill is None:
        print('[스킬] 대중적인 새 스킬이 없어 AI 도구 사용법 글로 대신합니다.')
        return None
    print(f'[스킬] {skill.key} (설치 {skill.installs:,}, 별 {skill.stars:,})')
    article = skill_topic.to_article(skill, now)
    theme = topics.SPECS[topics.SKILL].themes[0]
    draft = draft_article(llm, models, article, theme, past, is_alive, topics.SKILL, day=day)
    if draft is None:
        return None
    draft = force_skill_labels(draft)
    return Composed(
        article=article,
        draft=draft,
        related=history.related_posts(past, tuple(draft.labels), topic=f'{draft.search_keyword} {draft.title}'),
        footer_url=footer_url,
        category=topics.SKILL,
        skill=skill,
    )


def compose_guide_post(
    llm: LLMClient,
    models: Models,
    now: datetime,
    past: tuple[history.HistoryEntry, ...],
    get: HttpGet = http_get,
    is_alive: Callable[[str], bool] = url_is_alive,
    footer_url: str | None = DEFAULT_FOOTER_URL,
    day: date | None = None,
    limit: int = MAX_GUIDE_ATTEMPTS,
) -> Composed | None:
    """12:30 칸 예비 글: 대중적인 AI 도구의 공식 도움말로 사용법 글을 씁니다. 못 쓰면 None."""
    theme = topics.SPECS[topics.GUIDE].themes[0]
    fetch = lambda candidate: fetcher.fetch_article(get, candidate)  # noqa: E731
    for tried, (guide, article) in enumerate(guide_topic.iter_guides(past, fetch, now), start=1):
        print(f'[사용법 글] {guide.tool} · {guide.keyword} ({guide.url})')
        draft = draft_article(llm, models, article, theme, past, is_alive, topics.GUIDE, day=day)
        if draft is not None:
            draft = force_first_label(draft, topics.GUIDE_LABEL)
            return Composed(
                article=article,
                draft=draft,
                related=history.related_posts(past, tuple(draft.labels), topic=f'{draft.search_keyword} {draft.title}'),
                footer_url=footer_url,
                category=topics.GUIDE,
            )
        if tried >= limit:
            break
    return None


def compose_slot_post(
    llm: LLMClient,
    models: Models,
    now: datetime,
    past: tuple[history.HistoryEntry, ...],
    get: HttpGet = http_get,
    is_alive: Callable[[str], bool] = url_is_alive,
    footer_url: str | None = DEFAULT_FOOTER_URL,
    day: date | None = None,
) -> Composed:
    """12:30 칸: 스킬 글 → AI 도구 사용법 글 → (그래도 못 쓰면) AI·IT 뉴스. 칸은 절대 비우지 않는다."""
    kwargs = {'get': get, 'is_alive': is_alive, 'footer_url': footer_url, 'day': day}
    composed = compose_skill_post(llm, models, now, past, **kwargs)
    composed = composed or compose_guide_post(llm, models, now, past, **kwargs)
    if composed is not None:
        return composed
    print('[12:30 칸] 스킬·사용법 글을 모두 못 써 AI·IT 소식으로 발행합니다 (발행 건너뛰기 금지)')
    return compose_post(llm, models, now, past, only=topics.TECH, **kwargs)


def as_of_text(now: datetime) -> str:
    local = now.astimezone(schedule.KST)
    return f'{local.year}년 {local.month}월 {local.day}일'


def howto_tool(guide: howto_topic.HowtoGuide, draft: PostDraft) -> tools.Tool | None:
    """글감에 정해 둔 계산기, 없으면 글 내용으로 찾은 계산기 (생활경제 글과 같은 규칙)."""
    available = tools.load_tools()
    return tools.find_tool(available, guide.tool) or tools.match_tool(draft, available, topics.MONEY)


def howto_kind_rules(kind: str) -> str:
    """글 종류별 추가 규칙 (prompts/writer_howto_<kind>.md). 기존 안내형은 생활 가이드 규칙만."""
    return '' if kind == howto_topic.GUIDE else load_prompt(f'writer_howto_{kind}')


def compose_howto_post(
    llm: LLMClient,
    models: Models,
    now: datetime,
    past: tuple[history.HistoryEntry, ...],
    get: HttpGet = http_get,
    is_alive: Callable[[str], bool] = url_is_alive,
    footer_url: str | None = DEFAULT_FOOTER_URL,
    day: date | None = None,
    limit: int = MAX_HOWTO_ATTEMPTS,
    want: str = '',
) -> Composed | None:
    """생활 가이드 칸: 공식 안내 페이지로 검색형 생활 가이드 글을 씁니다. limit개 글감까지 써 보고 못 쓰면 None.
    want: 먼저 쓸 글 종류 (howto_topic.kind_for_slot)."""
    theme = topics.SPECS[topics.HOWTO].themes[0]
    fetch = lambda candidate: fetcher.fetch_article(get, candidate)  # noqa: E731
    for tried, (guide, article) in enumerate(howto_topic.iter_guides(past, fetch, now, want=want), start=1):
        print(f'[생활 가이드] {guide.kind} · {guide.query} (공식 안내 {1 + len(article.references)}곳)')
        draft = draft_article(llm, models, article, theme, past, is_alive, topics.HOWTO, target_query=guide.query, day=day,
                              kind_rules=howto_kind_rules(guide.kind))
        if draft is not None:
            labels = [topics.HOWTO_LABEL, guide.area] if guide.area != topics.HOWTO_LABEL else [topics.HOWTO_LABEL]
            if guide.kind in howto_topic.KIND_LABELS:
                labels = [*labels, howto_topic.KIND_LABELS[guide.kind]]
            draft = draft.model_copy(update={'labels': labels, 'url_slug': guide.slug or draft.url_slug})
            return Composed(
                article=article,
                draft=draft,
                related=history.related_posts(past, tuple(draft.labels), topic=f'{draft.search_keyword} {draft.title}'),
                footer_url=footer_url,
                tool=howto_tool(guide, draft),
                category=topics.HOWTO,
                as_of=as_of_text(now),
            )
        if tried >= limit:
            break
    return None


def compose_evening_post(
    llm: LLMClient,
    models: Models,
    now: datetime,
    past: tuple[history.HistoryEntry, ...],
    get: HttpGet = http_get,
    is_alive: Callable[[str], bool] = url_is_alive,
    footer_url: str | None = DEFAULT_FOOTER_URL,
    day: date | None = None,
    want: str = '',
) -> Composed:
    """생활 가이드 칸(08:00·20:00): 생활 가이드 → (못 쓰면) 평소 뉴스 글. 칸은 절대 비우지 않는다."""
    kwargs = {'get': get, 'is_alive': is_alive, 'footer_url': footer_url, 'day': day}
    try:
        composed = compose_howto_post(llm, models, now, past, want=want, **kwargs)
    except LLMError as e:
        print(f'[생활 가이드] AI 응답 오류: {e}')
        composed = None
    if composed is not None:
        return composed
    # 2026-10-18: 구글 블로그는 내 돈·날짜 글만 쓴다. 예비 발행도 생활경제 소식으로 (AI·IT 뉴스는 검색 클릭 0)
    print('[생활 가이드 칸] 생활 가이드 글을 못 써 생활경제 소식으로 발행합니다 (발행 건너뛰기 금지)')
    return compose_post(llm, models, now, past, only=topics.MONEY, **kwargs)


def compose_post(
    llm: LLMClient,
    models: Models,
    now: datetime,
    past: tuple[history.HistoryEntry, ...],
    get: HttpGet = http_get,
    is_alive: Callable[[str], bool] = url_is_alive,
    footer_url: str | None = DEFAULT_FOOTER_URL,
    day: date | None = None,
    only: str | None = None,
) -> Composed:
    """글 한 편을 완성합니다. 분야(AI·IT / 생활경제·재테크)를 최근 비율로 정하고, 상위 후보부터 차례로 시도합니다.

    고른 분야에 쓸 만한 후보가 없으면(원고를 써 보기 전이면) 다른 분야로 넘어간다. only를 주면 그 분야만.
    """
    first = only or topics.choose_category(past)
    order = (first,) if only else (first, topics.other(first))
    trending = trends.fetch_trending(get)
    print(f'[트렌드] 인기 키워드 {len(trending)}개')
    recent = tuple(e.title for e in past[-RECENT_TITLES_FOR_SELECTOR:])
    rankings: dict[str, tuple[str, list[RankedCandidate]]] = {}  # 분야 -> (테마, 낮춘 기준으로 매긴 순위)
    tried: set[str] = set()
    for relaxed in (False, True):
        for category in order:
            spec = topics.SPECS[category]
            if category not in rankings:
                theme = spec.themes[now.astimezone(auto_blogger.KST).weekday()]
                found = collector.fetch_candidates(get, now, spec.sources)
                candidates = [c for c in found if not selector.is_duplicate(c, past)]
                print(f'[분야] {spec.name} / 새 후보 {len(candidates)}건, 오늘의 테마: {theme}')
                every = selector.rank_candidates(
                    llm, models.fast, candidates, theme, spec.selector_prompt, recent, trending,
                    min_score=selector.FALLBACK_MIN_SCORE, day=day)
                rankings[category] = (theme, every)
            theme, every = rankings[category]
            ranked = [r for r in every if relaxed or r.score >= selector.MIN_SCORE]
            print(f'[선별] {spec.name} {"낮춘 " if relaxed else ""}기준 점수 이상 {len(ranked)}건')
            if relaxed and ranked:
                print('[선별] 기준 점수를 넘는 글감이 없어 기준을 낮춰 가장 나은 글감으로 씁니다')
            attempted = False
            for r in [r for r in ranked if r.candidate.link not in tried][:MAX_TOPIC_ATTEMPTS]:
                tried.add(r.candidate.link)
                print(f'[주제 시도] ({r.score}점) {r.candidate.title}')
                article = fetcher.fetch_article(get, r.candidate)
                if article is None:
                    continue
                article = fetcher.with_references(get, article, r.related)  # 기사 1건 재작성 대신 2~3개 비교
                attempted = True
                draft = draft_article(llm, models, article, theme, past, is_alive, category, day=day)
                if draft is None:
                    continue
                draft = drop_howto_label(draft, spec.labels[0])
                return Composed(
                    article=article,
                    draft=draft,
                    short_news=tuple(pick_short_news([x for x in every if x.score >= selector.MIN_SCORE], r.candidate.link)),
                    related=history.related_posts(past, tuple(draft.labels), topic=f'{draft.search_keyword} {draft.title}'),
                    footer_url=footer_url,
                    tool=tools.match_tool(draft, tools.load_tools(), category),
                    category=category,
                )
            if attempted:
                raise PipelineError(f'상위 {MAX_TOPIC_ATTEMPTS}개 주제 모두 원문 추출 또는 검수에 실패했습니다.')  # 원고까지 써 본 뒤에는 AI 호출 상한을 지키려고 더 넘어가지 않는다
            print(f'[분야] {spec.name}에서 쓸 주제가 없어 다른 분야로 넘어갑니다')
    raise PipelineError(f'상위 {MAX_TOPIC_ATTEMPTS}개 주제 모두 원문 추출 또는 검수에 실패했습니다.')


def drop_howto_label(draft: PostDraft, fallback: str) -> PostDraft:
    """뉴스 글에 생활가이드 라벨이 붙으면 뺀다 (분야 비율 계산에서 빠져 버리므로)."""
    if topics.HOWTO_LABEL not in draft.labels:
        return draft
    labels = [x for x in draft.labels if x != topics.HOWTO_LABEL] or [fallback]
    return draft.model_copy(update={'labels': labels})


def headline_of(draft: PostDraft, category: str = topics.TECH) -> thumbnail.Headline:
    """대표 이미지에 얹을 썸네일 문구. 왼쪽 위 라벨은 글의 첫 번째 라벨, 왼쪽 색은 그림 배경색(2026-10-21, 전엔 분야별)."""
    label = draft.labels[0] if draft.labels else ''
    return thumbnail.Headline(draft.thumbnail_text, draft.thumbnail_highlight, label)  # 왼쪽 색은 그림 배경색에서 뽑는다


def attach_hero_image(
    composed: Composed,
    backend: providers.ImageBackend,
    model: str,
    image_quality: str,
    r2: image.R2Config | None,
    key_date: str,
    dry_run: bool,
    make_s3: Callable[[image.R2Config], Any] = image.make_s3_client,
    preview_dir: Path = PREVIEW_PATH.parent,
    find_font: Callable[[], thumbnail.FontSpec | None] = thumbnail.find_font,
) -> Composed:
    """대표 이미지를 만들어 붙입니다. 실패해도 글 발행은 막지 않고 이미지 없이 진행합니다."""
    if not dry_run and r2 is None:
        print('[이미지 생략] R2 설정이 없어 이미지 없이 진행합니다. (.env의 R2_* 값 확인)')
        return composed
    try:
        raw = image.generate_image(backend, model, image_quality, composed.draft.image_prompt)
        titled = thumbnail.add_headline(raw, headline_of(composed.draft, composed.category), find_font())
        data = image.optimize_image(thumbnail.add_mascot(titled, thumbnail.mascot_for(composed.category)))
        if dry_run:
            preview_dir.mkdir(parents=True, exist_ok=True)
            (preview_dir / PREVIEW_IMAGE_NAME).write_bytes(data)
            url = PREVIEW_IMAGE_NAME
        else:
            url = image.upload_to_r2(make_s3(r2), r2, image.object_key(key_date, composed.draft.title), data)
    except IMAGE_ERRORS as e:
        print(f'[이미지 실패] {e.__class__.__name__}: {e} → 이미지 없이 진행합니다.')
        return composed
    print(f'[이미지] {len(data) // 1024}KB → {url}')
    return replace(composed, hero_image_url=url)


BODY_IMAGE_COUNT = 1  # 본문 중간 일러스트 수 (10/13 2장 → 10/15 사용자: 그림이 많아 1장, 인포그래픽 1장과 함께)


def attach_body_images(
    composed: Composed,
    backend: providers.ImageBackend,
    model: str,
    image_quality: str,
    r2: image.R2Config | None,
    key_date: str,
    dry_run: bool,
    make_s3: Callable[[image.R2Config], Any] = image.make_s3_client,
    preview_dir: Path = PREVIEW_PATH.parent,
) -> Composed:
    """본문 중간 그림 1~2장을 만들어 붙입니다. 한 장이 실패해도 나머지와 글 발행은 계속합니다."""
    if not dry_run and r2 is None:
        return composed
    sections = composed.draft.sections
    prompts = [p.strip() for p in composed.draft.body_image_prompts if p.strip()][:BODY_IMAGE_COUNT]
    made: list[tuple[str, str]] = []
    for n, prompt in enumerate(prompts, start=1):
        alt = sections[min(renderer.body_image_slots(len(sections), len(prompts))[n - 1], len(sections) - 1)].heading
        try:
            raw = image.generate_body_image(backend, model, image_quality, prompt)
            data = image.optimize_image(raw, image.BODY_WIDTH, image.BODY_MAX_BYTES)
            if dry_run:
                preview_dir.mkdir(parents=True, exist_ok=True)
                url = f'body{n}.webp'
                (preview_dir / url).write_bytes(data)
            else:
                url = image.upload_to_r2(make_s3(r2), r2, image.object_key(key_date, composed.draft.title, f'-b{n}'), data)
        except IMAGE_ERRORS as e:
            print(f'[본문 이미지 {n} 실패] {e.__class__.__name__}: {e} → 이 그림은 건너뜁니다.')
            continue
        print(f'[본문 이미지 {n}] {len(data) // 1024}KB → {url}')
        made.append((url, alt))
    return replace(composed, body_images=tuple(made)) if made else composed


def attach_infographics(
    composed: Composed,
    r2: image.R2Config | None,
    key_date: str,
    dry_run: bool,
    make_s3: Callable[[image.R2Config], Any] = image.make_s3_client,
    preview_dir: Path = PREVIEW_PATH.parent,
    find_font: Callable[[], thumbnail.FontSpec | None] = thumbnail.find_font,
) -> Composed:
    """글 내용으로 요약·체크리스트 인포그래픽을 그려 붙입니다 (2026-10-15). 실패해도 글은 그대로 발행합니다."""
    if not dry_run and r2 is None:
        return composed
    font = find_font()
    if font is None:
        print('[인포그래픽 생략] 굵은 한글 글꼴이 없습니다.')
        return composed
    tint = thumbnail.tint_for(composed.category)
    made: list[tuple[str, str, str]] = []
    for card in infographic.cards_for(composed.draft):
        try:
            data = image.optimize_image(infographic.render(card, composed.draft.search_keyword, tint, font),
                                        image.BODY_WIDTH, image.BODY_MAX_BYTES)
            name = f'info-{card.kind}.webp'
            if dry_run:
                preview_dir.mkdir(parents=True, exist_ok=True)
                (preview_dir / name).write_bytes(data)
                url = name
            else:
                url = image.upload_to_r2(make_s3(r2), r2, image.object_key(key_date, composed.draft.title, f'-{card.kind}'), data)
        except IMAGE_ERRORS as e:
            print(f'[인포그래픽 {card.kind} 실패] {e.__class__.__name__}: {e} → 건너뜁니다.')
            continue
        print(f'[인포그래픽 {card.kind}] {len(data) // 1024}KB → {url}')
        made.append((card.kind, url, f'{composed.draft.search_keyword} {card.title}'))
    return replace(composed, infographics=tuple(made)) if made else composed


# --- 발행 ---

def make_entry(composed: Composed, result: dict, publish_date: str, slot: str = '') -> history.HistoryEntry:
    return history.HistoryEntry(
        publish_date=publish_date,
        post_id=result['id'],
        title=composed.draft.title,
        # 초안은 Blogger가 글 주소 대신 블로그 첫 화면 주소를 돌려주므로 저장하지 않는다
        url='' if result.get('status') == 'DRAFT' else result.get('url', ''),
        source_url=composed.article.source_url,
        geeknews_url=composed.article.candidate.link,
        labels=tuple(composed.draft.labels),
        status=result.get('status', ''),
        slot=slot,
        tool=tools.tool_id(composed.tool) if composed.tool else '',
    )


def publish(
    service: Any,
    config: auto_blogger.Config,
    composed: Composed,
    target: Target,
    past: tuple[history.HistoryEntry, ...],
    path: Path | None = None,
) -> dict[str, Any]:
    """초안을 만들자마자 이력에 기록한 뒤 예약/발행합니다.

    예약 단계가 실패해도 초안이 이력에 남아 있으므로, 다음 실행이 같은 슬롯의 초안을 또 만들지 않습니다.
    """
    title = composed.draft.title
    # 초안(draft)은 사람이 검토하므로 한글 제목 그대로 둡니다.
    url_title = auto_blogger.slug_title(composed.draft.url_slug) if target.action != 'draft' else None
    body = auto_blogger.build_post_body(url_title or title, composed.html, composed.draft.labels)
    draft = auto_blogger.create_draft(service, config.blog_id, body)
    history.save_history(history.append_entry(past, make_entry(composed, draft, target.publish_date, target.slot)), path)
    print(f"[초안 저장] 글 ID {draft['id']}")
    if target.action == 'draft':
        return draft

    publish_at = target.publish_at if target.action == 'schedule' else None
    result = auto_blogger.publish_post(service, config.blog_id, draft['id'], publish_at)
    if url_title:
        result = auto_blogger.rename_post(service, config.blog_id, draft['id'], title)
    history.save_history(
        history.append_entry(past, make_entry(composed, result, target.publish_date, target.slot)), path
    )
    return result


def plan_targets(
    mode: str,
    now: datetime,
    times: tuple[schedule.Time, ...],
    done: set[str],
    at: tuple[int, int] | None = None,
) -> list[Target]:
    """모드에 따라 이번 실행에서 만들 글 목록을 정합니다."""
    stamp = now.astimezone(schedule.KST).strftime('%Y%m%d-%H%M%S')
    if mode == 'auto':
        return [Target(schedule.slot_key(s), s, 'schedule') for s in schedule.due_slots(now, times, done)]
    if mode == 'schedule':
        slot = schedule.next_open_slot(now, (at,) if at else times, done)
        return [Target(schedule.slot_key(slot), slot, 'schedule')] if slot else []
    if mode == 'draft':
        return [Target(f'manual-draft-{stamp}', None, 'draft')]
    if mode == 'now':
        return [Target(f'manual-now-{stamp}', None, 'now')]
    if mode == 'preview':
        return [Target('preview', None, 'preview')]
    raise ValueError(f'알 수 없는 모드: {mode}')


def success_message(composed: Composed, result: dict[str, Any], action: str, edit_link: str) -> str:
    headline = {'schedule': '✅ 글 예약 완료', 'now': '🚀 글 즉시 발행 완료', 'draft': '📝 글 초안 저장 완료'}
    lines = [
        headline.get(action, '✅ 완료'),
        f'제목: {composed.draft.title}',
        f'라벨: {", ".join(composed.draft.labels)}',
        f'대표 이미지: {"있음" if composed.hero_image_url else "없음 ⚠️"}',
    ]
    if action == 'schedule':
        lines.append(f"예약 시각: {result.get('published', '')}")
        lines.append('문제가 있으면 편집 화면에서 "초안으로 되돌리기"를 누르세요.')
    if action in ('schedule', 'now'):
        lines.append(f"공개 주소: {result.get('url', '')}")
    lines.append(f'편집/미리보기: {edit_link}')
    lines.append(f'원문: {composed.article.source_url}')
    if composed.tool:
        lines.append(f'🧮 계산기 링크: {composed.tool.title}')
    elif composed.draft.tool_idea.strip():
        lines.append(f'🧮 계산기 아이디어: {composed.draft.tool_idea.strip()}')
    return '\n'.join(lines)


def write_preview(composed: Composed) -> Path:
    PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    title = html.escape(composed.draft.title)
    page = (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        f'<title>{title}</title>'
        '<style>body{max-width:760px;margin:40px auto;padding:0 16px;font-family:sans-serif;line-height:1.7}</style>'
        f'</head><body><h1>{title}</h1>{composed.html}</body></html>'
    )
    PREVIEW_PATH.write_text(page, encoding='utf-8')
    return PREVIEW_PATH


# --- 실행 ---

@dataclass(frozen=True)
class RunResult:
    outcome: str
    message: str
    posts: tuple[runlog.PostRecord, ...] = ()
    llm_calls: int = 0


HIGH_LLM_CALLS_PER_POST = 10  # 평소 글당 3회 안팎. 이 이상이면 원고가 검수에 자주 떨어져 다시 쓰고 있다는 뜻


def llm_calls_warning(result: RunResult, writer_model: str) -> str | None:
    """글당 AI 호출이 많으면 알림 문구를, 아니면 None을 반환합니다. 글 없는 실패 실행은 1편으로 칩니다."""
    count = max(len(result.posts), 1)
    if result.llm_calls < HIGH_LLM_CALLS_PER_POST * count:
        return None
    return (f'⚠️ AI 호출이 많았습니다: {result.llm_calls}회 ({count}편, 평소 글당 3회 안팎)\n'
            f'원고 모델({writer_model})이 검수에 자주 떨어져 다시 쓰는 중일 수 있습니다. '
            '반복되면 원고 모델을 점검하세요.')


def is_paused(env: Mapping[str, str]) -> bool:
    return (env.get('PAUSED') or '').strip().lower() in ('1', 'true', 'yes', 'on')


def run(
    mode: str,
    at: tuple[int, int] | None,
    env: Mapping[str, str],
    ai: providers.AISettings,
    telegram: notifier.TelegramConfig | None,
    now: datetime | None = None,
) -> RunResult:
    now = now or datetime.now(auto_blogger.UTC)
    if mode == 'auto' and is_paused(env):
        return RunResult('skipped', '일시정지 상태라 자동 발행을 건너뜁니다.')

    past = history.load_history()
    times = schedule.parse_times(env.get('PUBLISH_TIMES'))
    skill_time = schedule.parse_times(env.get('SKILL_TIME') or DEFAULT_SKILL_TIME)[0]
    howto_times = schedule.parse_times(env.get('HOWTO_TIME') or DEFAULT_HOWTO_TIME)
    targets = plan_targets(mode, now, times, history.done_slots(past), at)
    if not targets:
        return RunResult('skipped', '지금 준비할 발행 시각이 없습니다.')
    print(f'[계획] {len(targets)}편: ' + ', '.join(t.slot for t in targets))

    dry_run = mode == 'preview'
    config = None if dry_run else auto_blogger.load_config(env)
    r2 = image.load_r2_config(env)
    text_backend = providers.build_text_backend(ai.text_provider, env)
    image_backend = providers.build_image_backend(ai.image_provider, env)
    models = Models(ai.writer_model, ai.fast_model)
    footer_url = (env.get('FOOTER_LINK_URL', DEFAULT_FOOTER_URL) or '').strip() or None
    service = None

    posts: list[runlog.PostRecord] = []
    errors: list[str] = []
    calls = 0
    for target in targets:
        llm = LLMClient(text_backend)
        day = date.fromisoformat(target.publish_date)  # 글이 읽히는 날 (예약이면 발행일)
        try:
            if is_skill_slot(target.publish_at, skill_time):
                composed = compose_slot_post(llm, models, now, past, footer_url=footer_url, day=day)
            elif any(is_skill_slot(target.publish_at, t) for t in howto_times):
                composed = compose_evening_post(llm, models, now, past, footer_url=footer_url, day=day,
                                                want=howto_topic.kind_for_slot(target.publish_at))
            else:
                composed = compose_post(llm, models, now, past, footer_url=footer_url, day=day)
            composed = attach_hero_image(
                composed, image_backend, ai.image_model, ai.image_quality, r2, target.publish_date, dry_run
            )
            composed = attach_body_images(
                composed, image_backend, ai.image_model, ai.image_quality, r2, target.publish_date, dry_run
            )
            composed = attach_infographics(composed, r2, target.publish_date, dry_run)
            if dry_run:
                print(f'[미리보기] {write_preview(composed)}')
                posts.append(runlog.PostRecord(composed.draft.title, 'PREVIEW', target.slot, '', '',
                                               bool(composed.hero_image_url)))
                continue
            service = service or auto_blogger.get_blogger_service(config)
            result = publish(service, config, composed, target, past)
            past = history.load_history()
            edit_link = auto_blogger.edit_url(config.blog_id, result['id'])
            print(f"[발행] {target.slot} status={result.get('status')} 편집: {edit_link}")
            notifier.notify(telegram, success_message(composed, result, target.action, edit_link))
            posts.append(runlog.PostRecord(
                composed.draft.title, result.get('status', ''), target.slot,
                '' if result.get('status') == 'DRAFT' else result.get('url', ''), edit_link,
                bool(composed.hero_image_url),
            ))
        except (PipelineError, LLMError) as e:
            errors.append(f'{target.slot}: {e}')
            print(f'[실패] {target.slot}: {e}')
        finally:
            calls += llm.calls

    if errors:
        return RunResult('failed', ' / '.join(errors), tuple(posts), calls)
    return RunResult('success', f'{len(posts)}편 완료', tuple(posts), calls)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='직장인 인사이트 글 자동 생성/발행')
    parser.add_argument('--mode', choices=MODES, default='preview')
    parser.add_argument('--dry-run', action='store_true', help='--mode preview와 같음')
    parser.add_argument('--at', help='schedule 모드에서 예약할 시각 HH:MM (KST)')
    args = parser.parse_args(argv)
    mode = 'preview' if args.dry_run else args.mode
    try:
        at = auto_blogger.parse_hhmm(args.at) if args.at else None
    except ValueError as e:
        parser.error(str(e))

    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
    env = os.environ
    telegram = notifier.load_telegram_config(env)
    started = datetime.now(schedule.KST)
    ai: providers.AISettings | None = None
    try:
        ai = providers.load_ai_settings(env)
        print(f'[AI] 글: {ai.text_provider} {ai.writer_model} / 검수: {ai.fast_model} / 이미지: {ai.image_model}')
        result = run(mode, at, env, ai, telegram)
    except (auto_blogger.ConfigError, image.ImageConfigError, providers.ProviderConfigError,
            schedule.ScheduleError, PipelineError, LLMError) as e:
        result = RunResult('failed', str(e))
    except HttpError as e:
        result = RunResult('failed', f'Blogger API 오류 HTTP {e.resp.status}: {e.reason}')
    except Exception as e:  # noqa: BLE001 - 무인 실행이므로 예상 못한 오류도 로그를 남기고 반드시 알린다
        traceback.print_exc()
        result = RunResult('failed', f'예상하지 못한 오류 {e.__class__.__name__}: {e}')

    print(f'[결과] {result.outcome}: {result.message}')
    if result.outcome == 'failed' and mode != 'preview':
        notifier.notify(telegram, f'❌ 글 발행 실패 ({mode})\n원인: {result.message}\n관리 화면의 실행 기록을 확인하세요.')
    warning = llm_calls_warning(result, ai.writer_model) if ai and mode != 'preview' else None
    if warning:
        print(warning)
        notifier.notify(telegram, warning)
    if env.get('GITHUB_ACTIONS') == 'true':
        runlog.append_run(runlog.RunRecord(
            usage=usage.snapshot(),
            stages=usage.stage_snapshot(),
            started_at=started.isoformat(timespec='seconds'),
            finished_at=datetime.now(schedule.KST).isoformat(timespec='seconds'),
            mode=mode,
            outcome=result.outcome,
            message=result.message,
            text_provider=ai.text_provider if ai else '',
            writer_model=ai.writer_model if ai else '',
            image_model=ai.image_model if ai else '',
            llm_calls=result.llm_calls,
            posts=result.posts,
            **runlog.github_context(env),
        ))
    return 1 if result.outcome == 'failed' else 0


if __name__ == '__main__':
    sys.exit(main())
