"""매일 1편: 수집 → 선별 → 원문 → 작성 → 검수 → 렌더링 → (초안/예약) 발행 → 이력 기록.

사용법:
    python -m src.main --dry-run            # Blogger에 올리지 않고 output/preview.html만 생성
    python -m src.main                      # 초안으로 저장 (검수 모드)
    python -m src.main --mode schedule      # PUBLISH_HOUR(기본 20시) KST에 예약 발행
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
from src import collector, fetcher, history, image, notifier, quality, renderer, selector, writer
from src.http_client import HttpGet, http_get, url_is_alive
from src.llm import LLMClient, LLMError
from src.models import Article, PostDraft, RankedCandidate, ShortNews

try:  # 이미지 단계에서 잡을 외부 라이브러리 오류
    from botocore.exceptions import BotoCoreError, ClientError
    from openai import OpenAIError
    IMAGE_ERRORS: tuple[type[Exception], ...] = (OpenAIError, BotoCoreError, ClientError, OSError, ValueError)
except ImportError:  # pragma: no cover
    IMAGE_ERRORS = (OSError, ValueError)

DEFAULT_WRITER_MODEL = 'gpt-5.5'
DEFAULT_FAST_MODEL = 'gpt-5.4-mini'
MAX_TOPIC_ATTEMPTS = 3
MAX_WRITE_ATTEMPTS = 2
SHORT_NEWS_COUNT = 3
DEFAULT_FOOTER_URL = 'https://link.inpock.co.kr/shoperwoody'
PREVIEW_PATH = Path(__file__).resolve().parent.parent / 'output' / 'preview.html'
PREVIEW_IMAGE_NAME = 'hero.webp'


class PipelineError(Exception):
    """오늘 발행할 글을 만들지 못했을 때 발생합니다."""


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


def load_models(env: Mapping[str, str]) -> Models:
    return Models(
        writer=env.get('OPENAI_MODEL_WRITER', DEFAULT_WRITER_MODEL),
        fast=env.get('OPENAI_MODEL_FAST', DEFAULT_FAST_MODEL),
    )


def draft_article(
    llm: LLMClient,
    models: Models,
    article: Article,
    theme: str,
    past: tuple[history.HistoryEntry, ...],
    is_alive: Callable[[str], bool],
) -> PostDraft | None:
    """글을 쓰고 검수합니다. 탈락하면 검수 의견을 반영해 한 번 더 씁니다."""
    feedback: tuple[str, ...] = ()
    for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
        draft = writer.write_post(llm, models.writer, article, theme, feedback)
        report = quality.run_quality_gate(llm, models.fast, draft, article, past, is_alive)
        if report.passed:
            print(f'[검수 통과] {draft.title} (시도 {attempt})')
            return draft
        print(f'[검수 탈락] 시도 {attempt}: ' + ' / '.join(report.issues))
        feedback = report.issues
    return None


def pick_short_news(ranked: Sequence[RankedCandidate], used_url: str) -> list[ShortNews]:
    others = [r for r in ranked if r.candidate.geeknews_url != used_url]
    return [ShortNews(r.candidate.title, r.one_line, r.candidate.geeknews_url) for r in others[:SHORT_NEWS_COUNT]]


def compose_post(
    llm: LLMClient,
    models: Models,
    now: datetime,
    past: tuple[history.HistoryEntry, ...],
    get: HttpGet = http_get,
    is_alive: Callable[[str], bool] = url_is_alive,
    footer_url: str | None = DEFAULT_FOOTER_URL,
) -> Composed:
    """오늘 글 한 편을 완성합니다. 상위 후보부터 차례로 시도합니다."""
    theme = selector.WEEKDAY_THEMES[now.astimezone(auto_blogger.KST).weekday()]
    candidates = [c for c in collector.fetch_candidates(get, now) if not selector.is_duplicate(c, past)]
    print(f'[수집] 새 후보 {len(candidates)}건, 오늘의 테마: {theme}')

    ranked = selector.rank_candidates(llm, models.fast, candidates, theme)
    print(f'[선별] 기준 점수 이상 {len(ranked)}건')
    if not ranked:
        raise PipelineError('오늘 쓸 만한 주제가 없습니다.')

    for r in ranked[:MAX_TOPIC_ATTEMPTS]:
        print(f'[주제 시도] ({r.score}점) {r.candidate.title}')
        article = fetcher.fetch_article(get, r.candidate)
        if article is None:
            continue
        draft = draft_article(llm, models, article, theme, past, is_alive)
        if draft is None:
            continue
        return Composed(
            article=article,
            draft=draft,
            short_news=tuple(pick_short_news(ranked, r.candidate.geeknews_url)),
            related=history.related_posts(past, tuple(draft.labels)),
            footer_url=footer_url,
        )
    raise PipelineError(f'상위 {MAX_TOPIC_ATTEMPTS}개 주제 모두 원문 추출 또는 검수에 실패했습니다.')


def make_entry(composed: Composed, result: dict, publish_date: str) -> history.HistoryEntry:
    return history.HistoryEntry(
        publish_date=publish_date,
        post_id=result['id'],
        title=composed.draft.title,
        # 초안은 Blogger가 글 주소 대신 블로그 첫 화면 주소를 돌려주므로 저장하지 않는다
        url='' if result.get('status') == 'DRAFT' else result.get('url', ''),
        source_url=composed.article.source_url,
        geeknews_url=composed.article.candidate.geeknews_url,
        labels=tuple(composed.draft.labels),
        status=result.get('status', ''),
    )


def publish(
    service: Any,
    config: auto_blogger.Config,
    composed: Composed,
    mode: str,
    past: tuple[history.HistoryEntry, ...],
    publish_date: str,
    now: datetime | None,
    at: tuple[int, int] | None,
    path: Path = history.DEFAULT_HISTORY_PATH,
) -> dict[str, Any]:
    """초안을 만들자마자 이력에 기록한 뒤 예약합니다.

    예약 단계가 실패해도 초안이 이력에 남아 있으므로, 다음 실행이 같은 날 초안을 또 만들지 않습니다.
    """
    body = auto_blogger.build_post_body(composed.draft.title, composed.html, composed.draft.labels)
    draft = auto_blogger.create_draft(service, config.blog_id, body)
    with_draft = history.append_entry(past, make_entry(composed, draft, publish_date))
    history.save_history(with_draft, path)
    print(f"[초안 저장] 글 ID {draft['id']}")
    if mode == 'draft':
        return draft

    result = auto_blogger.publish_existing(service, config, draft['id'], mode, now, at)
    history.save_history(history.append_entry(past, make_entry(composed, result, publish_date)), path)
    return result


def attach_hero_image(
    composed: Composed,
    openai_client: Any,
    settings: image.ImageSettings,
    r2: image.R2Config | None,
    publish_date: str,
    dry_run: bool,
    make_s3: Callable[[image.R2Config], Any] = image.make_s3_client,
    preview_dir: Path = PREVIEW_PATH.parent,
) -> Composed:
    """대표 이미지를 만들어 붙입니다. 실패해도 글 발행은 막지 않고 이미지 없이 진행합니다."""
    if not dry_run and r2 is None:
        print('[이미지 생략] R2 설정이 없어 이미지 없이 진행합니다. (.env의 R2_* 값 확인)')
        return composed
    try:
        raw = image.generate_image(openai_client, settings, composed.draft.image_prompt)
        data = image.optimize_image(raw)
        if dry_run:
            preview_dir.mkdir(parents=True, exist_ok=True)
            (preview_dir / PREVIEW_IMAGE_NAME).write_bytes(data)
            url = PREVIEW_IMAGE_NAME
        else:
            url = image.upload_to_r2(make_s3(r2), r2, image.object_key(publish_date, composed.draft.title), data)
    except IMAGE_ERRORS as e:
        print(f'[이미지 실패] {e.__class__.__name__}: {e} → 이미지 없이 진행합니다.')
        return composed
    print(f'[이미지] {len(data) // 1024}KB → {url}')
    return replace(composed, hero_image_url=url)


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


def success_message(composed: Composed, result: dict[str, Any], mode: str, edit_link: str) -> str:
    lines = [
        '✅ 오늘 글 준비 완료' if mode == 'schedule' else '📝 오늘 글 초안 저장 완료',
        f'제목: {composed.draft.title}',
        f'라벨: {", ".join(composed.draft.labels)}',
        f'대표 이미지: {"있음" if composed.hero_image_url else "없음 ⚠️"}',
    ]
    if mode == 'schedule':
        lines.append(f"예약 시각: {result.get('published', '')}")
        lines.append(f"공개 주소: {result.get('url', '')}")
        lines.append('문제가 있으면 편집 화면에서 "초안으로 되돌리기"를 누르세요.')
    lines.append(f'편집/미리보기: {edit_link}')
    lines.append(f'원문: {composed.article.source_url}')
    return '\n'.join(lines)


def build_openai() -> Any:
    from openai import OpenAI

    if not os.environ.get('OPENAI_API_KEY'):
        raise auto_blogger.ConfigError('OPENAI_API_KEY가 없습니다. .env 파일을 확인하세요.')
    return OpenAI()


def run(mode: str, dry_run: bool, at: tuple[int, int] | None, telegram: notifier.TelegramConfig | None) -> int:
    config = auto_blogger.load_config(os.environ)
    models = load_models(os.environ)
    now = datetime.now(auto_blogger.UTC)
    hour, minute = at or (config.publish_hour, 0)
    publish_date = auto_blogger.next_publish_time(now, hour, minute).date().isoformat()

    past = history.load_history()
    if not dry_run and history.has_entry_for(past, publish_date):
        print(f'[건너뜀] {publish_date} 발행분이 이미 있습니다.')
        return 0

    r2 = image.load_r2_config(os.environ)
    openai_client = build_openai()
    llm = LLMClient(openai_client)
    footer_url = os.environ.get('FOOTER_LINK_URL', DEFAULT_FOOTER_URL).strip() or None
    composed = compose_post(llm, models, now, past, footer_url=footer_url)
    print(f'[LLM] 이번 실행 호출 {llm.calls}회')
    composed = attach_hero_image(
        composed, openai_client, image.load_image_settings(os.environ), r2, publish_date, dry_run
    )

    preview = write_preview(composed)
    print(f'[미리보기] {preview}')
    if dry_run:
        return 0

    service = auto_blogger.get_blogger_service(config)
    result = publish(service, config, composed, mode, past, publish_date, now, at)
    print(f"[발행] mode={mode}, status={result.get('status')}")
    if mode == 'schedule':
        print(f"예약 시각: {result.get('published')}")
    edit_link = auto_blogger.edit_url(config.blog_id, result['id'])
    print(f'편집 화면: {edit_link}')
    notifier.notify(telegram, success_message(composed, result, mode, edit_link))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='직장인 인사이트 글 자동 생성/발행')
    parser.add_argument('--mode', choices=['draft', 'schedule'], default='draft')
    parser.add_argument('--dry-run', action='store_true', help='Blogger에 올리지 않고 미리보기만 생성')
    parser.add_argument('--at', help='예약 시각 HH:MM (KST). 없으면 PUBLISH_HOUR 정각')
    args = parser.parse_args(argv)
    try:
        at = auto_blogger.parse_hhmm(args.at) if args.at else None
    except ValueError as e:
        parser.error(str(e))

    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
    telegram = notifier.load_telegram_config(os.environ)
    try:
        return run(args.mode, args.dry_run, at, telegram)
    except (auto_blogger.ConfigError, image.ImageConfigError, PipelineError, LLMError) as e:
        reason = str(e)
    except HttpError as e:
        reason = f'Blogger API 오류 HTTP {e.resp.status}: {e.reason}'
    except Exception as e:  # noqa: BLE001 - 무인 실행이므로 예상 못한 오류도 로그를 남기고 반드시 알린다
        traceback.print_exc()
        reason = f'예상하지 못한 오류 {e.__class__.__name__}: {e}'
    print(f'[실패] {reason}')
    if not args.dry_run:
        notifier.notify(telegram, f'❌ 오늘 글 발행 실패\n원인: {reason}\nGitHub Actions 로그를 확인하세요.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
