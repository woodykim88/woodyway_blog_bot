"""매일 오전 화제 글 즉시 발행 (mode=trend, 2026-11-26 사용자 결정 "계속" → 화제는 식기 전에).

급상승 검색어는 보통 하루 안에 식는다. 예약(20:00, 월수금) 칸을 기다리지 않고 오전에 바로 발행한다.
- 하루 1편: 슬롯 키 trend-YYYY-MM-DD가 이미 있으면 건너뛴다 (cron 지연·중복 실행 대비).
- 쓸 만한 화제가 없으면 건너뛴다. 이 칸은 덤이라 '발행 건너뛰기 금지' 대상이 아니다 (정규 칸은 따로 있다).
- 끄기: TREND_DAILY=off. 일시정지(PAUSED)도 따른다.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime

import auto_blogger
from src import history, image, notifier, providers, runlog, schedule
from src import main as pipeline


def enabled(env: Mapping[str, str]) -> bool:
    return (env.get('TREND_DAILY') or 'on').strip().lower() not in ('0', 'false', 'no', 'off')


def slot_key(now: datetime) -> str:
    return f'trend-{now.astimezone(schedule.KST).date().isoformat()}'


def run_trend(env: Mapping[str, str], ai: providers.AISettings, telegram: notifier.TelegramConfig | None,
              now: datetime | None = None) -> pipeline.RunResult:
    now = now or datetime.now(auto_blogger.UTC)
    if pipeline.is_paused(env) or not enabled(env):
        return pipeline.RunResult('skipped', '화제 글이 꺼져 있습니다 (PAUSED 또는 TREND_DAILY=off).')
    past = history.load_history()
    slot = slot_key(now)
    if slot in history.done_slots(past):
        return pipeline.RunResult('skipped', '오늘 화제 글은 이미 발행했습니다.')

    llm = pipeline.LLMClient(providers.build_text_backend(ai.text_provider, env))
    footer_url = (env.get('FOOTER_LINK_URL', pipeline.DEFAULT_FOOTER_URL) or '').strip() or None
    today = now.astimezone(schedule.KST).date()
    try:
        composed = pipeline.compose_trend_post(llm, pipeline.Models(ai.writer_model, ai.fast_model), now, past,
                                               footer_url=footer_url, day=today)
        if composed is None:
            return pipeline.RunResult('skipped', '오늘은 이 블로그에 맞는 화제가 없습니다.', (), llm.calls)
        return _publish(composed, env, ai, slot, today, past, llm.calls)
    except (pipeline.PipelineError, pipeline.LLMError) as e:
        return pipeline.RunResult('failed', f'{slot}: {e}', (), llm.calls)


def _publish(composed: pipeline.Composed, env: Mapping[str, str], ai: providers.AISettings, slot: str,
             today: date, past: tuple[history.HistoryEntry, ...], calls: int) -> pipeline.RunResult:
    r2 = image.load_r2_config(env)
    backend = providers.build_image_backend(ai.image_provider, env)
    key = today.isoformat()
    composed = pipeline.attach_hero_image(composed, backend, ai.image_model, ai.image_quality, r2, key, False)
    composed = pipeline.attach_body_images(composed, backend, ai.image_model, ai.image_quality, r2, key, False)
    composed = pipeline.attach_infographics(composed, r2, key, False)
    config = auto_blogger.load_config(env)
    result = pipeline.publish(auto_blogger.get_blogger_service(config), config, composed,
                              pipeline.Target(slot, None, 'now'), past)
    edit_link = auto_blogger.edit_url(config.blog_id, result['id'])
    print(pipeline.success_message(composed, result, 'now', edit_link))  # 성공은 알리지 않는다 (실패만 알림)
    post = runlog.PostRecord(composed.draft.title, result.get('status', ''), slot, result.get('url', ''), edit_link,
                             bool(composed.hero_image_url))
    return pipeline.RunResult('success', '화제 글 1편 발행', (post,), calls)
