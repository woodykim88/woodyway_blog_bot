"""OpenAI 한도 소진 시 GPTproto로 넘어가는 대비 계층 테스트."""
from __future__ import annotations

import io

import pytest

from src import openai_fallback as fb


class ApiError(Exception):
    def __init__(self, status_code: int, code: str | None = None, message: str = 'x') -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class Leaf:
    def __init__(self, name: str, calls: list, error: Exception | None = None) -> None:
        self._name, self._calls, self._error = name, calls, error

    def parse(self, **kwargs):
        self._calls.append((self._name, kwargs))
        if self._error:
            raise self._error
        return f'{self._name}-ok'


class FakeClient:
    def __init__(self, name: str, calls: list, error: Exception | None = None) -> None:
        self.responses = Leaf(name, calls, error)
        self.images = Leaf(name, calls, error)


def make(primary_error=None, fallback_error=None, fallback=True):
    calls: list = []
    primary = FakeClient('primary', calls, primary_error)
    second = FakeClient('gptproto', calls, fallback_error) if fallback else None
    return fb.FallbackClient(primary, second), calls


@pytest.mark.parametrize('exc', [
    ApiError(429, 'insufficient_quota', 'You exceeded your current quota'),
    ApiError(429, None, 'Quota exceeded, check billing'),
    ApiError(400, 'billing_hard_limit_reached'),
    ApiError(401, 'invalid_api_key'),
    ApiError(403),
    ApiError(402),
])
def test_exhaustion_errors_switch_to_gptproto(exc):
    client, calls = make(primary_error=exc)
    assert client.responses.parse(model='m') == 'gptproto-ok'
    assert [c[0] for c in calls] == ['primary', 'gptproto']


@pytest.mark.parametrize('exc', [
    ApiError(429, 'rate_limit_exceeded', 'Rate limit reached, slow down'),
    ApiError(500),
    ApiError(400, 'invalid_request_error'),
    ValueError('boom'),
])
def test_other_errors_are_not_swallowed(exc):
    client, calls = make(primary_error=exc)
    with pytest.raises(type(exc)):
        client.responses.parse(model='m')
    assert [c[0] for c in calls] == ['primary']


def test_switch_is_sticky_for_the_process():
    client, calls = make(primary_error=ApiError(429, 'insufficient_quota'))
    client.responses.parse(model='m')
    client.images.parse(model='m')
    assert [c[0] for c in calls] == ['primary', 'gptproto', 'gptproto']
    assert client.using_fallback


def test_no_fallback_key_keeps_original_error():
    client, _ = make(primary_error=ApiError(429, 'insufficient_quota'), fallback=False)
    with pytest.raises(ApiError):
        client.responses.parse(model='m')


def test_fallback_failure_is_raised():
    client, _ = make(primary_error=ApiError(429, 'insufficient_quota'), fallback_error=ApiError(500))
    with pytest.raises(ApiError) as info:
        client.responses.parse(model='m')
    assert info.value.status_code == 500


def test_file_arguments_are_rewound_before_retry():
    class Reader(Leaf):
        def parse(self, **kwargs):
            data = kwargs['image'].read()
            self._calls.append((self._name, data))
            if self._error:
                raise self._error
            return 'ok'

    calls: list = []
    primary = FakeClient('primary', calls, ApiError(429, 'insufficient_quota'))
    primary.responses = Reader('primary', calls, ApiError(429, 'insufficient_quota'))
    second = FakeClient('gptproto', calls)
    second.responses = Reader('gptproto', calls)
    client = fb.FallbackClient(primary, second)
    client.responses.parse(image=io.BytesIO(b'png-bytes'))
    assert calls == [('primary', b'png-bytes'), ('gptproto', b'png-bytes')]


def test_primary_used_normally_when_healthy():
    client, calls = make()
    assert client.responses.parse(model='m') == 'primary-ok'
    assert [c[0] for c in calls] == ['primary']
    assert not client.using_fallback


class Recorder:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def test_make_openai_builds_both_clients(monkeypatch):
    env = {'OPENAI_API_KEY': 'sk-open', 'GPTPROTO_API_KEY': 'sk-proto'}
    client = fb.make_openai(env, factory=Recorder, timeout=5)
    # GPTproto가 기본, OpenAI가 예비 (2026-10-13)
    assert client._primary.kwargs == {'api_key': 'sk-proto', 'base_url': fb.DEFAULT_BASE_URL, 'timeout': 5}
    assert client._fallback.kwargs == {'api_key': 'sk-open', 'timeout': 5}


def test_make_openai_custom_base_url_and_no_fallback():
    env = {'OPENAI_API_KEY': 'sk-open', 'GPTPROTO_API_KEY': 'sk-proto', 'GPTPROTO_BASE_URL': 'https://x.test/v1'}
    assert fb.make_openai(env, factory=Recorder)._primary.kwargs['base_url'] == 'https://x.test/v1'
    assert fb.make_openai({'OPENAI_API_KEY': 'sk-open'}, factory=Recorder)._fallback is None


def test_make_openai_without_openai_key_uses_gptproto_only():
    client = fb.make_openai({'GPTPROTO_API_KEY': 'sk-proto'}, factory=Recorder)
    assert not client.using_fallback
    assert client._fallback is None


def _png_bytes() -> bytes:
    Image = pytest.importorskip('PIL.Image')
    buffer = io.BytesIO()
    Image.new('RGBA', (8, 8), (255, 0, 0, 255)).save(buffer, format='PNG')
    return buffer.getvalue()


@pytest.mark.parametrize('fmt,magic', [('jpeg', b'\xff\xd8'), ('webp', b'RIFF')])
def test_to_image_format_converts_png(fmt, magic):
    assert fb.to_image_format(_png_bytes(), fmt).startswith(magic)


def test_to_image_format_keeps_matching_bytes():
    jpeg = fb.to_image_format(_png_bytes(), 'jpeg')
    assert fb.to_image_format(jpeg, 'jpeg') is jpeg


def test_to_image_format_leaves_non_png_untouched():
    assert fb.to_image_format(b'not-an-image', 'jpeg') == b'not-an-image'
