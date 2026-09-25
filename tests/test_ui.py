import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ui import catalog, envfile
from ui import github as gh

APP = str(Path(__file__).resolve().parent.parent / 'ui' / 'app.py')


# --- GitHubClient ---

def fake_response(status=200, body=None, text=None):
    content = json.dumps(body).encode() if body is not None else (text or '').encode()
    return SimpleNamespace(
        status_code=status, content=content,
        headers={'content-type': 'application/json' if body is not None else 'text/plain'},
        json=lambda: body, text=text if text is not None else json.dumps(body),
    )


def test_client_set_variable_creates_or_updates():
    session = MagicMock()
    session.request.side_effect = [fake_response(404), fake_response(201, {})]
    gh.GitHubClient('o/r', 't', session).set_variable('PUBLISH_TIMES', '20:00')
    assert [c.args[0] for c in session.request.call_args_list] == ['GET', 'POST']

    session.request.side_effect = [fake_response(200, {'name': 'X'}), fake_response(204)]
    gh.GitHubClient('o/r', 't', session).set_variable('X', '1')
    assert session.request.call_args.args[0] == 'PATCH'


def test_client_set_variables_only_changes_diff():
    client = gh.GitHubClient('o/r', 't', MagicMock())
    client.list_variables = lambda: {'A': '1', 'B': '2'}
    client.set_variable = MagicMock()
    client.set_variables({'A': '1', 'B': '3', 'C': '4'})
    assert [c.args for c in client.set_variable.call_args_list] == [('B', '3'), ('C', '4')]


def test_client_dispatch_payload():
    session = MagicMock()
    session.request.return_value = fake_response(204)
    gh.GitHubClient('o/r', 't', session).dispatch('schedule', '21:00')
    kwargs = session.request.call_args.kwargs
    assert kwargs['json'] == {'ref': 'main', 'inputs': {'mode': 'schedule', 'at': '21:00'}}
    assert kwargs['headers']['Authorization'] == 'Bearer t'


def test_client_errors_become_korean_messages():
    session = MagicMock()
    session.request.return_value = fake_response(403, {'message': 'x'})
    with pytest.raises(gh.GitHubError, match='HTTP 403'):
        gh.GitHubClient('o/r', 't', session).list_variables()


def test_client_read_file_returns_raw_text_even_for_json():
    body = [{'title': '글'}]
    session = MagicMock()
    session.request.return_value = fake_response(200, body)  # GitHub가 JSON content-type으로 응답
    text = gh.GitHubClient('o/r', 't', session).read_file('data/history.json')
    assert json.loads(text) == body


def test_client_read_file_missing_returns_none():
    session = MagicMock()
    session.request.return_value = fake_response(404)
    assert gh.GitHubClient('o/r', 't', session).read_file('data/runs.jsonl') is None


def test_set_secret_passes_value_via_stdin(monkeypatch):
    calls = []
    monkeypatch.setattr(gh.subprocess, 'run', lambda args, **kw: calls.append((args, kw)) or SimpleNamespace(returncode=0))
    gh.GitHubClient('o/r', 't').set_secret('OPENAI_API_KEY', 'sk-secret')
    args, kw = calls[0]
    assert 'sk-secret' not in args and kw['input'] == 'sk-secret'


def test_key_log_lines():
    log = ('post\tGenerate\t2026-09-27T06:17:01.1Z [수집] 새 후보 30건\n'
           'post\tGenerate\t2026-09-27T06:17:02.1Z pip install\n'
           'post\tGenerate\t2026-09-27T06:17:03.1Z [검수 통과] 제목\n')
    assert gh.key_log_lines(log) == ['[수집] 새 후보 30건', '[검수 통과] 제목']


# --- catalog / envfile ---

def test_catalog_filters():
    ids = ['gpt-5.5', 'gpt-image-2', 'gpt-4o-realtime', 'text-embedding-3', 'gemini-2.5-flash-image',
           'imagen-4.0-generate-001', 'gemini-2.5-pro']
    assert catalog.filter_text_models(ids) == ('gpt-5.5', 'gemini-2.5-pro')
    assert set(catalog.filter_image_models(ids)) == {'gpt-image-2', 'gemini-2.5-flash-image', 'imagen-4.0-generate-001'}


def test_check_key_empty_and_failure(monkeypatch):
    assert catalog.check_key('openai', '  ')[0] is False
    monkeypatch.setitem(catalog.FETCHERS, 'openai', lambda key: (_ for _ in ()).throw(RuntimeError('401')))
    ok, message = catalog.check_key('openai', 'bad')
    assert not ok and 'RuntimeError' in message and 'bad' not in message


def test_envfile_update_preserves_other_lines(tmp_path):
    path = tmp_path / '.env'
    path.write_text('# comment\nBLOG_ID=1\nOPENAI_API_KEY=old\n')
    envfile.update_env({'OPENAI_API_KEY': 'new', 'GEMINI_API_KEY': 'g'}, path)
    assert path.read_text() == '# comment\nBLOG_ID=1\nOPENAI_API_KEY=new\nGEMINI_API_KEY=g\n'
    assert envfile.read_env(path) == {'BLOG_ID': '1', 'OPENAI_API_KEY': 'new', 'GEMINI_API_KEY': 'g'}
    assert oct(path.stat().st_mode & 0o777) == '0o600'


# --- 화면 (AppTest) ---

class FakeClient:
    repo = 'o/r'

    def __init__(self):
        self.dispatched = []
        self.saved = {}

    def list_variables(self):
        return {'PUBLISH_TIMES': '08:00,20:00', 'PAUSED': 'false'}

    def list_secrets(self):
        return {'OPENAI_API_KEY': '2026-09-25T13:00:00Z'}

    def read_file(self, path):
        if path == 'data/history.json':
            return json.dumps([{'publish_date': '2026-09-26', 'slot': '2099-09-26T20:00', 'post_id': '1',
                                'title': '예약 글', 'url': 'https://w/x.html', 'source_url': 'https://s',
                                'geeknews_url': 'https://g', 'labels': ['AI활용'], 'status': 'SCHEDULED'}])
        return json.dumps({'started_at': '2026-09-27T15:17:00+09:00', 'trigger': 'schedule', 'mode': 'auto',
                           'outcome': 'success', 'message': '1편 완료', 'run_id': '5', 'run_url': 'https://r',
                           'posts': [{'title': '예약 글'}]}) + '\n'

    def list_runs(self):
        return [{'id': 5, 'event': 'schedule', 'status': 'completed', 'conclusion': 'success',
                 'created_at': '2026-09-27T06:17:00Z', 'url': 'https://r'}]

    def dispatch(self, mode, at=None):
        self.dispatched.append((mode, at))

    def set_variables(self, values):
        self.saved.update(values)


@pytest.fixture
def fake_client(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr(gh, 'gh_token', lambda: 't')
    monkeypatch.setattr(gh, 'GitHubClient', lambda repo, token: client)
    monkeypatch.setattr(envfile, 'ENV_PATH', tmp_path / '.env')
    st.cache_resource.clear()
    st.cache_data.clear()
    return client


def open_page(label):
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value(label).run()
    return at


@pytest.mark.parametrize('label', ['🏠 대시보드', '📜 실행 기록', '📰 발행 글', '⏰ 발행 설정', '🧠 AI 설정', '🔑 API 키'])
def test_every_page_renders_without_error(fake_client, label):
    at = open_page(label)
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]


def test_dashboard_shows_state_and_dispatches_preview(fake_client):
    at = open_page('🏠 대시보드')
    assert at.metric[0].value == '08:00 · 20:00'
    assert '예약 글' in at.info[0].value
    next(b for b in at.button if b.label == '👀 미리보기 만들기').click().run()
    assert fake_client.dispatched == [('preview', None)]


def test_dashboard_immediate_publish_requires_confirmation(fake_client):
    at = open_page('🏠 대시보드')
    publish = next(b for b in at.button if b.label == '🚀 지금 즉시 발행')
    assert publish.disabled
    at.checkbox[0].check().run()
    next(b for b in at.button if b.label == '🚀 지금 즉시 발행').click().run()
    assert fake_client.dispatched == [('now', None)]


def test_schedule_page_saves_times(fake_client):
    at = open_page('⏰ 발행 설정')
    next(b for b in at.button if b.label == '💾 저장').click().run()
    assert fake_client.saved['PUBLISH_TIMES'] == '08:00,20:00'
    assert fake_client.saved['PAUSED'] == 'false'
