from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from src import history, http_client
from src import main as pipeline
from src.llm import DEFAULT_MAX_CALLS
from tests.helpers import make_article, make_draft

# --- SSRF 방지 ---

def fake_resolver(mapping):
    def resolve(host):
        return mapping[host]
    return resolve


@pytest.mark.parametrize('url, ips, ok', [
    ('https://example.com/a', ['93.184.216.34'], True),
    ('http://169.254.169.254/latest/meta-data', ['169.254.169.254'], False),
    ('http://localhost/x', ['127.0.0.1'], False),
    ('http://internal.corp/x', ['10.0.0.5'], False),
    ('https://mixed.example/x', ['93.184.216.34', '192.168.0.1'], False),
    ('https://v6.example/x', ['::1'], False),
    ('ftp://example.com/a', ['93.184.216.34'], False),
])
def test_is_public_url(url, ips, ok):
    host = url.split('/')[2]
    assert http_client.is_public_url(url, resolve=fake_resolver({host: ips})) is ok


def test_is_public_url_unresolvable_host():
    def resolve(host):
        raise OSError('no such host')
    assert not http_client.is_public_url('https://nope.invalid/', resolve=resolve)


def test_http_get_blocks_redirect_to_private_address(monkeypatch):
    responses = iter([
        SimpleNamespace(is_redirect=True, headers={'location': 'http://169.254.169.254/'}, status_code=302,
                        text='', close=lambda: None, raise_for_status=lambda: None),
    ])
    monkeypatch.setattr(http_client.requests, 'get', lambda *a, **k: next(responses))
    resolve = fake_resolver({'example.com': ['93.184.216.34'], '169.254.169.254': ['169.254.169.254']})
    with pytest.raises(http_client.UnsafeUrlError):
        http_client.http_get('https://example.com/a', resolve=resolve)


def test_http_get_follows_safe_redirect(monkeypatch):
    responses = iter([
        SimpleNamespace(is_redirect=True, headers={'location': '/b'}, status_code=301,
                        text='', close=lambda: None, raise_for_status=lambda: None),
        SimpleNamespace(is_redirect=False, headers={}, status_code=200,
                        text='ok', close=lambda: None, raise_for_status=lambda: None),
    ])
    calls = []
    monkeypatch.setattr(http_client.requests, 'get', lambda url, **k: calls.append(url) or next(responses))
    assert http_client.http_get('https://example.com/a', resolve=fake_resolver({'example.com': ['93.184.216.34']})) == 'ok'
    assert calls == ['https://example.com/a', 'https://example.com/b']


def test_url_is_alive_false_for_private_address():
    assert not http_client.url_is_alive('http://127.0.0.1/', resolve=fake_resolver({'127.0.0.1': ['127.0.0.1']}))


# --- LLM 호출 상한 ---

def test_llm_budget_covers_worst_case():
    worst = 1 + pipeline.MAX_TOPIC_ATTEMPTS * pipeline.MAX_WRITE_ATTEMPTS * 2
    assert DEFAULT_MAX_CALLS >= worst


# --- 초안 생성 직후 이력 기록 ---

COMPOSED = pipeline.Composed(make_article(), make_draft())


def _config():
    import auto_blogger
    return auto_blogger.load_config({'BLOG_ID': '123'})


def test_publish_records_draft_before_scheduling_and_updates_status(tmp_path):
    path = tmp_path / 'history.json'
    service = MagicMock()
    service.posts().insert().execute.return_value = {'id': '42', 'status': 'DRAFT'}
    service.posts().publish().execute.return_value = {
        'id': '42', 'status': 'SCHEDULED', 'url': 'https://w/x.html', 'published': '2026-09-26T20:00:00+09:00'
    }
    result = pipeline.publish(service, _config(), COMPOSED, 'schedule', (), '2026-09-26', None, None, path)
    assert result['status'] == 'SCHEDULED'
    saved = history.load_history(path)
    assert len(saved) == 1
    assert saved[0].status == 'SCHEDULED' and saved[0].url == 'https://w/x.html'


def test_publish_failure_keeps_draft_in_history(tmp_path):
    path = tmp_path / 'history.json'
    service = MagicMock()
    service.posts().insert().execute.return_value = {'id': '42', 'status': 'DRAFT'}
    resp = MagicMock(status=403, reason='Forbidden')
    service.posts().publish().execute.side_effect = HttpError(resp, b'{}')
    with pytest.raises(HttpError):
        pipeline.publish(service, _config(), COMPOSED, 'schedule', (), '2026-09-26', None, None, path)
    saved = history.load_history(path)
    assert [(e.post_id, e.status) for e in saved] == [('42', 'DRAFT')]
    assert history.has_entry_for(saved, '2026-09-26')  # 다음 실행이 초안을 또 만들지 않음
