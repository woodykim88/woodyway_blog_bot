"""AI 회사(OpenAI / Google Gemini / Anthropic Claude) 연결 계층.

파이프라인은 TextBackend.parse()와 ImageBackend.generate()만 알면 되고,
어느 회사를 쓰는지는 환경변수(TEXT_PROVIDER, IMAGE_PROVIDER 등)로 정한다.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel
from src import usage

T = TypeVar('T', bound=BaseModel)

TEXT_PROVIDERS = ('openai', 'gemini', 'anthropic')
IMAGE_PROVIDERS = ('openai', 'gemini')
PROVIDER_LABELS = {'openai': 'OpenAI (GPT)', 'gemini': 'Google Gemini', 'anthropic': 'Anthropic Claude'}
KEY_ENV = {'openai': 'OPENAI_API_KEY', 'gemini': 'GEMINI_API_KEY', 'anthropic': 'ANTHROPIC_API_KEY'}

DEFAULT_TEXT_MODELS = {
    'openai': {'writer': 'gpt-5.5', 'fast': 'gpt-5.4-mini'},
    'gemini': {'writer': 'gemini-2.5-pro', 'fast': 'gemini-2.5-flash'},
    'anthropic': {'writer': 'claude-sonnet-5', 'fast': 'claude-haiku-4-5'},
}
DEFAULT_IMAGE_MODELS = {'openai': 'gpt-image-2', 'gemini': 'imagen-4.0-generate-001'}
IMAGE_QUALITIES = ('low', 'medium', 'high')
ANTHROPIC_MAX_TOKENS = 8000
IMAGE_SIZE = '1536x1024'


REASONING_MODEL_PREFIXES = ('gpt-5', 'o')  # 생각하는 양(reasoning effort)을 받는 OpenAI 모델


def reasoning_option(model: str, effort: str | None) -> dict:
    """effort를 받는 모델에만 reasoning 옵션을 붙입니다 (안 받는 모델에 붙이면 요청이 거절된다)."""
    if not effort or not model.startswith(REASONING_MODEL_PREFIXES):
        return {}
    return {'reasoning': {'effort': effort}}


class ProviderConfigError(Exception):
    """AI 회사/모델/키 설정이 잘못되었을 때 발생합니다."""


@dataclass(frozen=True)
class AISettings:
    text_provider: str
    writer_model: str
    fast_model: str
    image_provider: str
    image_model: str
    image_quality: str


def load_ai_settings(env: Mapping[str, str]) -> AISettings:
    """환경변수에서 AI 설정을 읽습니다. 예전 이름(OPENAI_MODEL_WRITER 등)도 호환합니다."""
    text_provider = (env.get('TEXT_PROVIDER') or 'openai').strip().lower()
    image_provider = (env.get('IMAGE_PROVIDER') or 'openai').strip().lower()
    if text_provider not in TEXT_PROVIDERS:
        raise ProviderConfigError(f'TEXT_PROVIDER는 {"/".join(TEXT_PROVIDERS)} 중 하나여야 합니다. (현재: {text_provider})')
    if image_provider not in IMAGE_PROVIDERS:
        raise ProviderConfigError(f'IMAGE_PROVIDER는 {"/".join(IMAGE_PROVIDERS)} 중 하나여야 합니다. (현재: {image_provider})')

    legacy = text_provider == 'openai'
    defaults = DEFAULT_TEXT_MODELS[text_provider]
    writer = env.get('WRITER_MODEL') or (env.get('OPENAI_MODEL_WRITER') if legacy else None) or defaults['writer']
    fast = env.get('FAST_MODEL') or (env.get('OPENAI_MODEL_FAST') if legacy else None) or defaults['fast']
    image_legacy = image_provider == 'openai'
    image_model = (
        env.get('IMAGE_MODEL')
        or (env.get('OPENAI_IMAGE_MODEL') if image_legacy else None)
        or DEFAULT_IMAGE_MODELS[image_provider]
    )
    quality = (env.get('IMAGE_QUALITY') or env.get('OPENAI_IMAGE_QUALITY') or 'medium').strip().lower()
    if quality not in IMAGE_QUALITIES:
        raise ProviderConfigError(f'IMAGE_QUALITY는 {"/".join(IMAGE_QUALITIES)} 중 하나여야 합니다. (현재: {quality})')
    return AISettings(text_provider, writer.strip(), fast.strip(), image_provider, image_model.strip(), quality)


def api_key(env: Mapping[str, str], provider: str) -> str:
    key = (env.get(KEY_ENV[provider]) or '').strip()
    if not key:
        raise ProviderConfigError(f'{PROVIDER_LABELS[provider]}를 쓰려면 {KEY_ENV[provider]}가 필요합니다.')
    return key


# --- 텍스트 ---

class TextBackend(Protocol):
    def parse(
        self, model: str, instructions: str, user_input: str, schema: type[T], effort: str | None = None
    ) -> T | None: ...


class OpenAITextBackend:
    def __init__(self, client: Any) -> None:
        self._client = client

    def parse(
        self, model: str, instructions: str, user_input: str, schema: type[T], effort: str | None = None
    ) -> T | None:
        response = self._client.responses.parse(
            model=model, instructions=instructions, input=user_input, text_format=schema, **reasoning_option(model, effort)
        )
        usage.record(model, response)
        return response.output_parsed


class AnthropicTextBackend:
    def __init__(self, client: Any) -> None:
        self._client = client

    def parse(
        self, model: str, instructions: str, user_input: str, schema: type[T], effort: str | None = None
    ) -> T | None:
        response = self._client.messages.parse(
            model=model,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            system=instructions,
            messages=[{'role': 'user', 'content': user_input}],
            output_format=schema,
        )
        usage.record(model, response)
        return response.parsed_output


class GeminiTextBackend:
    def __init__(self, client: Any) -> None:
        self._client = client

    def parse(
        self, model: str, instructions: str, user_input: str, schema: type[T], effort: str | None = None
    ) -> T | None:
        from google.genai import types

        response = self._client.models.generate_content(
            model=model,
            contents=user_input,
            config=types.GenerateContentConfig(
                system_instruction=instructions,
                response_mime_type='application/json',
                response_schema=schema,
            ),
        )
        parsed = response.parsed
        if parsed is None or isinstance(parsed, schema):
            return parsed
        return schema.model_validate(parsed)


def build_text_backend(provider: str, env: Mapping[str, str]) -> TextBackend:
    key = api_key(env, provider)
    if provider == 'openai':
        from openai import OpenAI
        return OpenAITextBackend(OpenAI(api_key=key))
    if provider == 'anthropic':
        from anthropic import Anthropic
        return AnthropicTextBackend(Anthropic(api_key=key))
    from google import genai
    return GeminiTextBackend(genai.Client(api_key=key))


# --- 이미지 ---

class ImageBackend(Protocol):
    def generate(self, model: str, prompt: str, quality: str) -> bytes: ...


class OpenAIImageBackend:
    def __init__(self, client: Any) -> None:
        self._client = client

    def generate(self, model: str, prompt: str, quality: str) -> bytes:
        import base64

        response = self._client.images.generate(
            model=model, prompt=prompt, size=IMAGE_SIZE, quality=quality, output_format='webp', n=1
        )
        usage.record(model, response, 'image')
        return base64.b64decode(response.data[0].b64_json)


class GeminiImageBackend:
    """imagen-* 모델은 generate_images, 그 외(gemini-*-image)는 generate_content로 이미지를 받는다."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def generate(self, model: str, prompt: str, quality: str) -> bytes:
        from google.genai import types

        if model.startswith('imagen'):
            response = self._client.models.generate_images(
                model=model,
                prompt=prompt,
                config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio='16:9'),
            )
            if not response.generated_images:
                raise ValueError('Gemini가 이미지를 반환하지 않았습니다 (안전 필터 가능성).')
            return response.generated_images[0].image.image_bytes

        response = self._client.models.generate_content(
            model=model,
            contents=f'{prompt}\nAspect ratio 16:9, landscape.',
            config=types.GenerateContentConfig(response_modalities=['IMAGE']),
        )
        for candidate in response.candidates or []:
            for part in (candidate.content.parts if candidate.content else None) or []:
                if part.inline_data and part.inline_data.data:
                    return part.inline_data.data
        raise ValueError('Gemini 응답에 이미지가 없습니다.')


def build_image_backend(provider: str, env: Mapping[str, str]) -> ImageBackend:
    key = api_key(env, provider)
    if provider == 'openai':
        from openai import OpenAI
        return OpenAIImageBackend(OpenAI(api_key=key))
    from google import genai
    return GeminiImageBackend(genai.Client(api_key=key))
