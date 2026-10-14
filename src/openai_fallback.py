"""GPTproto 중계 API를 먼저 쓰고, 거절되면 OpenAI로 넘어가는 클라이언트 감싸개.

make_openai(env)는 GPTPROTO_API_KEY가 있으면 GPTproto(OpenAI 호환)를 기본으로 쓰고, 호출이
한도 소진/결제/키 오류로 거절될 때 같은 요청을 OpenAI로 다시 보내 그 프로세스가 끝날 때까지 OpenAI를 씁니다.
GPTPROTO_API_KEY가 없으면 OPENAI_API_KEY만 쓰며 오류가 그대로 올라옵니다 (2026-10-13 기본 순서 변경).
"""
from __future__ import annotations

import io
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

PRIMARY_KEY_ENV = 'OPENAI_API_KEY'
FALLBACK_KEY_ENV = 'GPTPROTO_API_KEY'
FALLBACK_URL_ENV = 'GPTPROTO_BASE_URL'
DEFAULT_BASE_URL = 'https://api.gptproto.com/v1'

EXHAUSTED_CODES = frozenset({'insufficient_quota', 'billing_hard_limit_reached', 'billing_not_active', 'account_deactivated'})
EXHAUSTED_STATUSES = frozenset({401, 402, 403})


def is_exhausted(exc: Exception) -> bool:
    """충전금 소진/결제 문제/키 거절처럼 같은 키로는 계속 실패할 오류인지 판단합니다 (일시적 속도 제한은 제외)."""
    status = getattr(exc, 'status_code', None)
    code = str(getattr(exc, 'code', None) or '').lower()
    if status in EXHAUSTED_STATUSES or code in EXHAUSTED_CODES:
        return True
    text = str(exc).lower()
    return status == 429 and ('quota' in text or 'billing' in text)


TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
TRANSIENT_NAMES = frozenset({'APIConnectionError', 'APITimeoutError', 'ReadTimeout', 'ConnectTimeout', 'ConnectError'})
RETRY_DELAY = 3.0  # 일시적 오류 때 같은 API를 한 번 더 부르기 전 기다리는 초 (중계 과부하는 보통 금방 풀린다)


def is_transient(exc: Exception) -> bool:
    """과부하(429)·서버 오류(5xx)·연결/시간 초과처럼 잠깐 뒤 다시 하면 풀릴 수 있는 오류인지 판단합니다."""
    return getattr(exc, 'status_code', None) in TRANSIENT_STATUSES or type(exc).__name__ in TRANSIENT_NAMES


def _resolve(client: Any, path: tuple[str, ...]) -> Callable[..., Any]:
    target = client
    for name in path:
        target = getattr(target, name)
    return target


def _rewind(positions: list[tuple[Any, int]]) -> None:
    for handle, position in positions:
        handle.seek(position)


def _file_positions(args: tuple, kwargs: dict) -> list[tuple[Any, int]]:
    return [(v, v.tell()) for v in (*args, *kwargs.values()) if hasattr(v, 'seek') and hasattr(v, 'tell')]


class _Route:
    """client.responses.parse 같은 점 표기 경로를 기억했다가 호출 시점에 FallbackClient로 넘깁니다."""

    def __init__(self, owner: FallbackClient, path: tuple[str, ...]) -> None:
        self._owner = owner
        self._path = path

    def __getattr__(self, name: str) -> _Route:
        if name.startswith('__'):
            raise AttributeError(name)
        return _Route(self._owner, (*self._path, name))

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._owner.call(self._path, args, kwargs)


class FallbackClient:
    def __init__(self, primary: Any | None, fallback: Any | None) -> None:
        self._primary = primary
        self._fallback = fallback
        self._use_fallback = primary is None

    @property
    def using_fallback(self) -> bool:
        return self._use_fallback

    def __getattr__(self, name: str) -> _Route:
        if name.startswith('_'):
            raise AttributeError(name)
        return _Route(self, (name,))

    def call(self, path: tuple[str, ...], args: tuple, kwargs: dict) -> Any:
        if not self._use_fallback:
            positions = _file_positions(args, kwargs)
            try:
                return _resolve(self._primary, path)(*args, **kwargs)
            except Exception as exc:
                if self._fallback is None:
                    raise
                if is_exhausted(exc):
                    logger.warning('기본 API 사용 불가(%s: %s) -> 예비 API로 전환합니다.', type(exc).__name__, str(exc)[:120])
                    self._use_fallback = True
                    _rewind(positions)
                elif is_transient(exc):
                    # 2026-10-14: GPTproto 과부하(429)로 채널 두 곳이 멈춘 뒤 추가. 한 번 더 시도하고, 그래도 안 되면 이번 호출만 예비로 보낸다.
                    logger.warning('기본 API 일시 오류(%s: %s) -> 잠시 뒤 재시도합니다.', type(exc).__name__, str(exc)[:120])
                    _rewind(positions)
                    time.sleep(RETRY_DELAY)
                    try:
                        return _resolve(self._primary, path)(*args, **kwargs)
                    except Exception as retry_exc:
                        if not (is_transient(retry_exc) or is_exhausted(retry_exc)):
                            raise
                        logger.warning('재시도도 실패(%s) -> 이번 호출만 예비 API로 보냅니다.', type(retry_exc).__name__)
                        _rewind(positions)
                else:
                    raise
        return _resolve(self._fallback, path)(*args, **kwargs)


def make_openai(env: Mapping[str, str], factory: Callable[..., Any] | None = None, **client_kwargs: Any) -> FallbackClient:
    """OPENAI_API_KEY(기본)와 GPTPROTO_API_KEY(예비)로 클라이언트를 만듭니다. client_kwargs는 두 클라이언트에 공통 적용."""
    if factory is None:
        from openai import OpenAI as factory
    primary_key = (env.get(PRIMARY_KEY_ENV) or '').strip()
    fallback_key = (env.get(FALLBACK_KEY_ENV) or '').strip()
    if not primary_key and not fallback_key:
        raise ValueError(f'{PRIMARY_KEY_ENV} 또는 {FALLBACK_KEY_ENV}가 필요합니다.')
    openai_client = factory(api_key=primary_key, **client_kwargs) if primary_key else None
    base_url = (env.get(FALLBACK_URL_ENV) or DEFAULT_BASE_URL).strip()
    proto_client = factory(api_key=fallback_key, base_url=base_url, **client_kwargs) if fallback_key else None
    if proto_client is not None:
        # 2026-10-13 사용자 지시: GPTproto 중계 키를 먼저 쓰고, OpenAI는 GPTproto가 거절할 때의 예비
        return FallbackClient(proto_client, openai_client)
    return FallbackClient(openai_client, None)


PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def to_image_format(data: bytes, fmt: str) -> bytes:
    """GPTproto는 output_format을 무시하고 PNG를 돌려주므로, PNG가 오면 요청한 형식(jpeg/webp)으로 바꿉니다."""
    if not data.startswith(PNG_MAGIC):
        return data
    from PIL import Image

    with Image.open(io.BytesIO(data)) as source:
        image = source.convert('RGB')
    buffer = io.BytesIO()
    image.save(buffer, format=fmt.upper(), quality=90)
    return buffer.getvalue()
