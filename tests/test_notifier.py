from unittest.mock import MagicMock

import requests

from src import main as pipeline
from src import notifier
from tests.helpers import make_article, make_draft

CONFIG = notifier.TelegramConfig('123:ABC', '999')


def test_load_telegram_config():
    assert notifier.load_telegram_config({}) is None
    assert notifier.load_telegram_config({'TELEGRAM_BOT_TOKEN': 't'}) is None
    assert notifier.load_telegram_config({'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': '1'}) == \
        notifier.TelegramConfig('t', '1')


def test_notify_noop_without_config():
    post = MagicMock()
    assert notifier.notify(None, 'hi', post=post) is False
    post.assert_not_called()


def test_notify_sends_message():
    post = MagicMock()
    assert notifier.notify(CONFIG, 'hi', post=post) is True
    url = post.call_args.args[0]
    assert url == 'https://api.telegram.org/bot123:ABC/sendMessage'
    assert post.call_args.kwargs['json']['chat_id'] == '999'


def test_notify_failure_does_not_raise_or_leak_token(capsys):
    def failing_post(*args, **kwargs):
        raise requests.ConnectionError('https://api.telegram.org/bot123:ABC/sendMessage failed')
    assert notifier.notify(CONFIG, 'hi', post=failing_post) is False
    assert '123:ABC' not in capsys.readouterr().out


def test_success_message_schedule():
    composed = pipeline.Composed(make_article(), make_draft(), hero_image_url='https://img/x.webp')
    text = pipeline.success_message(
        composed, {'published': '2026-09-26T20:00:00+09:00', 'url': 'https://w/x.html'}, 'schedule', 'https://edit'
    )
    for part in (make_draft().title, '2026-09-26T20:00:00+09:00', 'https://w/x.html', 'https://edit', '있음'):
        assert part in text


def test_success_message_warns_missing_image():
    composed = pipeline.Composed(make_article(), make_draft())
    assert '없음' in pipeline.success_message(composed, {}, 'draft', 'https://edit')


def test_main_notifies_on_unexpected_error(monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, 'run', MagicMock(side_effect=RuntimeError('boom')))
    monkeypatch.setattr(pipeline.notifier, 'notify', lambda cfg, text, **k: sent.append(text))
    monkeypatch.setattr(pipeline, 'load_dotenv', lambda *a, **k: None)
    assert pipeline.main(['--mode', 'schedule']) == 1
    assert sent and 'boom' in sent[0]
