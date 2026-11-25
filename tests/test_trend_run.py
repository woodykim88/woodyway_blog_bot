"""매일 오전 화제 글 즉시 발행 (mode=trend)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src import history, providers, trend_run
from src import main as pipeline
from tests.helpers import make_article, make_draft, make_entry

NOW = datetime(2026, 11, 27, 0, 40, tzinfo=timezone.utc)  # KST 09:40
AI = providers.load_ai_settings({})


def _wire(monkeypatch, tmp_path, composed, past=()):
    path = tmp_path / 'history.json'
    history.save_history(tuple(past), path)
    monkeypatch.setattr(history, 'DEFAULT_HISTORY_PATH', path)
    monkeypatch.setattr(pipeline.providers, 'build_text_backend', lambda p, env: None)
    monkeypatch.setattr(pipeline.providers, 'build_image_backend', lambda p, env: None)
    monkeypatch.setattr(pipeline.auto_blogger, 'load_config', lambda env: SimpleNamespace(blog_id='1'))
    monkeypatch.setattr(pipeline.auto_blogger, 'get_blogger_service', lambda cfg: 'SVC')
    monkeypatch.setattr(pipeline.auto_blogger, 'edit_url', lambda b, i: 'edit')
    for name in ('attach_hero_image', 'attach_body_images'):
        monkeypatch.setattr(pipeline, name, lambda c, *a, **k: c)
    monkeypatch.setattr(pipeline, 'attach_infographics', lambda c, *a, **k: c)
    monkeypatch.setattr(pipeline, 'compose_trend_post', lambda *a, **k: composed)
    published = []

    def fake_publish(service, config, c, target, past):
        published.append(target)
        return {'id': '1', 'status': 'LIVE', 'url': 'https://www.woodyway88.com/x.html'}

    monkeypatch.setattr(pipeline, 'publish', fake_publish)
    return published


def test_trend_run_publishes_now_with_daily_slot(monkeypatch, tmp_path):
    published = _wire(monkeypatch, tmp_path, pipeline.Composed(make_article(), make_draft()))
    result = trend_run.run_trend({}, AI, None, now=NOW)
    assert result.outcome == 'success'
    assert published[0].slot == 'trend-2026-11-27' and published[0].action == 'now'
    assert result.posts[0].url == 'https://www.woodyway88.com/x.html'


def test_trend_run_skips_when_already_done_today(monkeypatch, tmp_path):
    past = (make_entry(slot='trend-2026-11-27'),)
    published = _wire(monkeypatch, tmp_path, pipeline.Composed(make_article(), make_draft()), past)
    assert trend_run.run_trend({}, AI, None, now=NOW).outcome == 'skipped'
    assert published == []


def test_trend_run_skips_without_good_trend(monkeypatch, tmp_path):
    published = _wire(monkeypatch, tmp_path, None)
    result = trend_run.run_trend({}, AI, None, now=NOW)
    assert result.outcome == 'skipped' and published == []


def test_trend_run_respects_pause_and_switch(monkeypatch, tmp_path):
    published = _wire(monkeypatch, tmp_path, pipeline.Composed(make_article(), make_draft()))
    assert trend_run.run_trend({'PAUSED': 'true'}, AI, None, now=NOW).outcome == 'skipped'
    assert trend_run.run_trend({'TREND_DAILY': 'off'}, AI, None, now=NOW).outcome == 'skipped'
    assert published == []


def test_main_routes_trend_mode(monkeypatch):
    monkeypatch.setattr(trend_run, 'run_trend', lambda env, ai, tg, now=None: pipeline.RunResult('skipped', 'x'))
    assert 'trend' in pipeline.MODES
    assert pipeline.run('trend', None, {}, AI, None).message == 'x'


def test_evening_trend_first_now_defaults_off():
    assert not pipeline.trend_first({})
    assert pipeline.trend_first({'TREND_FIRST': 'on'})
