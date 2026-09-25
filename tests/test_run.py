from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src import history, providers, schedule
from src import main as pipeline
from tests.helpers import make_article, make_draft

KST = schedule.KST
AI = providers.load_ai_settings({})
NOW = datetime(2026, 9, 27, 15, 0, tzinfo=KST)


# --- plan_targets ---

def test_plan_targets_auto_uses_due_slots():
    targets = pipeline.plan_targets('auto', NOW, ((8, 0), (20, 0)), set())
    assert [(t.slot, t.action) for t in targets] == [('2026-09-27T20:00', 'schedule')]
    assert targets[0].publish_date == '2026-09-27'


def test_plan_targets_schedule_with_explicit_time():
    targets = pipeline.plan_targets('schedule', NOW, ((20, 0),), set(), at=(9, 30))
    assert targets[0].slot == '2026-09-28T09:30'


def test_plan_targets_schedule_none_when_all_done():
    done = {'2026-09-27T20:00', '2026-09-28T20:00', '2026-09-29T20:00'}
    assert pipeline.plan_targets('schedule', NOW, ((20, 0),), done) == []


@pytest.mark.parametrize('mode, action', [('draft', 'draft'), ('now', 'now'), ('preview', 'preview')])
def test_plan_targets_manual_modes(mode, action):
    [target] = pipeline.plan_targets(mode, NOW, ((20, 0),), set())
    assert target.action == action and target.publish_at is None
    assert target.slot.startswith('manual-') or target.slot == 'preview'


# --- run() ---

@pytest.fixture
def wired(monkeypatch, tmp_path):
    """외부 호출을 모두 가짜로 바꾼 파이프라인."""
    hist_path = tmp_path / 'history.json'
    monkeypatch.setattr(history, 'DEFAULT_HISTORY_PATH', hist_path)
    monkeypatch.setattr(pipeline.providers, 'build_text_backend', lambda p, env: MagicMock())
    monkeypatch.setattr(pipeline.providers, 'build_image_backend', lambda p, env: MagicMock())
    composed = pipeline.Composed(make_article(), make_draft())
    compose = MagicMock(return_value=composed)
    monkeypatch.setattr(pipeline, 'compose_post', compose)
    monkeypatch.setattr(pipeline, 'attach_hero_image', lambda c, *a, **k: c)
    service = MagicMock()
    service.posts().insert().execute.return_value = {'id': '7', 'status': 'DRAFT'}
    service.posts().publish().execute.return_value = {'id': '7', 'status': 'SCHEDULED', 'url': 'https://w/p.html',
                                                       'published': '2026-09-27T20:00:00+09:00'}
    monkeypatch.setattr(pipeline.auto_blogger, 'get_blogger_service', lambda config: service)
    sent = []
    monkeypatch.setattr(pipeline.notifier, 'notify', lambda cfg, text, **k: sent.append(text))
    return {'service': service, 'compose': compose, 'sent': sent, 'hist_path': hist_path}


ENV = {'BLOG_ID': '123', 'PUBLISH_TIMES': '08:00,20:00', 'OPENAI_API_KEY': 'k'}


def test_run_auto_schedules_due_slot_and_records(wired):
    result = pipeline.run('auto', None, ENV, AI, None, now=NOW)
    assert result.outcome == 'success'
    assert [p.slot for p in result.posts] == ['2026-09-27T20:00']
    saved = history.load_history(wired['hist_path'])
    assert [(e.slot, e.status) for e in saved] == [('2026-09-27T20:00', 'SCHEDULED')]
    assert '글 예약 완료' in wired['sent'][0]


def test_run_auto_skips_when_already_done(wired):
    pipeline.run('auto', None, ENV, AI, None, now=NOW)
    result = pipeline.run('auto', None, ENV, AI, None, now=NOW)
    assert result.outcome == 'skipped'
    assert wired['compose'].call_count == 1


def test_run_auto_paused(wired):
    result = pipeline.run('auto', None, {**ENV, 'PAUSED': 'true'}, AI, None, now=NOW)
    assert result.outcome == 'skipped' and '일시정지' in result.message
    wired['compose'].assert_not_called()


def test_run_now_publishes_immediately(wired):
    result = pipeline.run('now', None, ENV, AI, None, now=NOW)
    assert result.outcome == 'success'
    wired['service'].posts().publish.assert_called_with(blogId='123', postId='7')
    assert '즉시 발행' in wired['sent'][0]
    # 즉시 발행은 정기 슬롯을 막지 않는다
    assert pipeline.plan_targets('auto', NOW, ((20, 0),), history.done_slots(history.load_history(wired['hist_path'])))


def test_run_multiple_slots_in_one_run(wired):
    now = datetime(2026, 9, 27, 3, 0, tzinfo=KST)
    env = {**ENV, 'PUBLISH_TIMES': '07:00,08:30'}
    result = pipeline.run('auto', None, env, AI, None, now=now)
    assert [p.slot for p in result.posts] == ['2026-09-27T07:00', '2026-09-27T08:30']
    assert wired['compose'].call_count == 2


def test_run_failure_of_one_slot_is_reported(wired):
    wired['compose'].side_effect = pipeline.PipelineError('주제 없음')
    result = pipeline.run('auto', None, ENV, AI, None, now=NOW)
    assert result.outcome == 'failed' and '주제 없음' in result.message


def test_main_writes_runlog_on_github(monkeypatch, tmp_path):
    runs = tmp_path / 'runs.jsonl'
    monkeypatch.setattr(pipeline.runlog, 'DEFAULT_RUNS_PATH', runs)
    monkeypatch.setattr(pipeline, 'load_dotenv', lambda *a, **k: None)
    monkeypatch.setattr(pipeline, 'run', lambda *a, **k: pipeline.RunResult('skipped', '없음'))
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'schedule')
    assert pipeline.main(['--mode', 'auto']) == 0
    row = pipeline.runlog.parse_runs(runs.read_text())[0]
    assert row['mode'] == 'auto' and row['outcome'] == 'skipped' and row['trigger'] == 'schedule'


def test_tests_never_touch_real_data_files():
    from src import history as h
    from src import runlog as r
    assert h.DEFAULT_HISTORY_PATH.parent.name != 'data'
    assert r.DEFAULT_RUNS_PATH.parent.name != 'data'
