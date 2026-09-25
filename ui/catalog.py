"""AI 회사별 사용 가능한 모델 목록 조회와 API 키 확인."""
from __future__ import annotations

from dataclasses import dataclass

TEXT_EXCLUDE = ('image', 'audio', 'realtime', 'tts', 'transcribe', 'embedding', 'search', 'moderation', 'whisper',
                'dall-e', 'codex', 'computer-use', 'live', 'veo', 'imagen', 'aqa', 'robotics')


@dataclass(frozen=True)
class Catalog:
    text_models: tuple[str, ...]
    image_models: tuple[str, ...]


def filter_text_models(ids: list[str]) -> tuple[str, ...]:
    return tuple(sorted({i for i in ids if not any(word in i for word in TEXT_EXCLUDE)}, reverse=True))


def filter_image_models(ids: list[str]) -> tuple[str, ...]:
    return tuple(sorted({i for i in ids if 'image' in i or i.startswith('imagen')}, reverse=True))


def _openai_ids(key: str) -> list[str]:
    from openai import OpenAI
    return [m.id for m in OpenAI(api_key=key).models.list() if m.id.startswith(('gpt-', 'chatgpt-image', 'o'))]


def _gemini_ids(key: str) -> list[str]:
    from google import genai
    ids = []
    for m in genai.Client(api_key=key).models.list():
        name = (m.name or '').removeprefix('models/')
        if name.startswith(('gemini', 'imagen')):
            ids.append(name)
    return ids


def _anthropic_ids(key: str) -> list[str]:
    from anthropic import Anthropic
    return [m.id for m in Anthropic(api_key=key).models.list(limit=100)]


FETCHERS = {'openai': _openai_ids, 'gemini': _gemini_ids, 'anthropic': _anthropic_ids}


def fetch_catalog(provider: str, key: str) -> Catalog:
    """키로 실제 모델 목록을 조회합니다. 키가 틀리면 해당 SDK 예외가 그대로 올라갑니다."""
    ids = FETCHERS[provider](key)
    images = filter_image_models(ids) if provider in ('openai', 'gemini') else ()
    return Catalog(text_models=filter_text_models(ids), image_models=images)


def check_key(provider: str, key: str) -> tuple[bool, str]:
    """키가 유효한지 확인합니다. (성공 여부, 사용자 메시지)"""
    if not key.strip():
        return False, '키가 비어 있습니다.'
    try:
        catalog = fetch_catalog(provider, key.strip())
    except Exception as e:  # noqa: BLE001 - 회사마다 예외 종류가 달라 모두 잡아 메시지로 바꾼다
        return False, f'키 확인 실패: {e.__class__.__name__}'
    return True, f'키 확인 완료 · 글쓰기 모델 {len(catalog.text_models)}개 사용 가능'
