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
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from googleapiclient.errors import HttpError

import auto_blogger
from src import (
    collector,
    fetcher,
    history,
    image,
    notifier,
    providers,
    quality,
    renderer,
    runlog,
    schedule,
    selector,
    thumbnail,
    topics,
    polish,
    writer,
)
from src.http_client import HttpGet, http_get, url_is_alive
from src.llm import LLMClient, LLMError
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
MAX_WRITE_ATTEMPTS = 2
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

    @property
    def html(self) -> str:
        return renderer.render_post(
            self.draft,
            self.article,
            short_news=self.short_news,
            related=self.related,
            hero_image_url=self.hero_image_url,
            footer_url=self.footer_url,
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

def draft_article(
    llm: LLMClient,
    models: Models,
    article: Article,
    theme: str,
    past: tuple[history.HistoryEntry, ...],
    is_alive: Callable[[str], bool],
    category: str = topics.TECH,
) -> PostDraft | None:
    """글을 쓰고 사람이 쓴 글처럼 윤문한 뒤 검수합니다. 탈락하면 검수 의견을 반영해 한 번 더 씁니다."""
    feedback: tuple[str, ...] = ()
    for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
        draft = polish.polish_draft(llm, models.writer, writer.write_post(llm, models.writer, article, theme, feedback, category))
        report = quality.run_quality_gate(llm, models.fast, draft, article, past, is_alive)
        if report.passed:
            print(f'[검수 통과] {draft.title} (시도 {attempt})')
            return draft
        print(f'[검수 탈락] 시도 {attempt}: ' + ' / '.join(report.issues))
        feedback = report.issues
    return None


def pick_short_news(ranked: Sequence[RankedCandidate], used_url: str) -> list[ShortNews]:
    others = [r for r in ranked if r.candidate.link != used_url]
    return [ShortNews(r.candidate.title, r.one_line, r.candidate.link) for r in others[:SHORT_NEWS_COUNT]]


def compose_post(
    llm: LLMClient,
    models: Models,
    now: datetime,
    past: tuple[history.HistoryEntry, ...],
    get: HttpGet = http_get,
    is_alive: Callable[[str], bool] = url_is_alive,
    footer_url: str | None = DEFAULT_FOOTER_URL,
) -> Composed:
    """글 한 편을 완성합니다. 분야(AI·IT / 생활경제·재테크)를 최근 비율로 정하고, 상위 후보부터 차례로 시도합니다.

    고른 분야에 쓸 만한 후보가 없으면(원고를 써 보기 전이면) 다른 분야로 넘어간다.
    """
    first = topics.choose_category(past)
    for category in (first, topics.other(first)):
        spec = topics.SPECS[category]
        theme = spec.themes[now.astimezone(auto_blogger.KST).weekday()]
        found = collector.fetch_candidates(get, now, spec.sources)
        candidates = [c for c in found if not selector.is_duplicate(c, past)]
        print(f'[분야] {spec.name} / 새 후보 {len(candidates)}건, 오늘의 테마: {theme}')
        ranked = selector.rank_candidates(llm, models.fast, candidates, theme, spec.selector_prompt)
        print(f'[선별] 기준 점수 이상 {len(ranked)}건')
        attempted = False
        for r in ranked[:MAX_TOPIC_ATTEMPTS]:
            print(f'[주제 시도] ({r.score}점) {r.candidate.title}')
            article = fetcher.fetch_article(get, r.candidate)
            if article is None:
                continue
            attempted = True
            draft = draft_article(llm, models, article, theme, past, is_alive, category)
            if draft is None:
                continue
            return Composed(
                article=article,
                draft=draft,
                short_news=tuple(pick_short_news(ranked, r.candidate.link)),
                related=history.related_posts(past, tuple(draft.labels)),
                footer_url=footer_url,
            )
        if attempted:
            break  # 원고까지 써 본 분야에서 실패했으면 AI 호출 상한을 지키려고 다른 분야로 넘어가지 않는다
        print(f'[분야] {spec.name}에서 쓸 주제가 없어 다른 분야로 넘어갑니다')
    raise PipelineError(f'상위 {MAX_TOPIC_ATTEMPTS}개 주제 모두 원문 추출 또는 검수에 실패했습니다.')


def headline_of(draft: PostDraft) -> thumbnail.Headline:
    """대표 이미지에 얹을 썸네일 문구. 왼쪽 위 라벨은 글의 첫 번째 라벨."""
    label = draft.labels[0] if draft.labels else ''
    return thumbnail.Headline(draft.thumbnail_text, draft.thumbnail_highlight, label)


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
        data = image.optimize_image(thumbnail.add_headline(raw, headline_of(composed.draft), find_font()))
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
    body = auto_blogger.build_post_body(composed.draft.title, composed.html, composed.draft.labels)
    draft = auto_blogger.create_draft(service, config.blog_id, body)
    history.save_history(history.append_entry(past, make_entry(composed, draft, target.publish_date, target.slot)), path)
    print(f"[초안 저장] 글 ID {draft['id']}")
    if target.action == 'draft':
        return draft

    publish_at = target.publish_at if target.action == 'schedule' else None
    result = auto_blogger.publish_post(service, config.blog_id, draft['id'], publish_at)
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
        try:
            composed = compose_post(llm, models, now, past, footer_url=footer_url)
            composed = attach_hero_image(
                composed, image_backend, ai.image_model, ai.image_quality, r2, target.publish_date, dry_run
            )
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
