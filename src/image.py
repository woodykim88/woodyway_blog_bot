"""대표 이미지 생성(OpenAI/Gemini) → 최적화(Pillow) → Cloudflare R2 업로드."""
from __future__ import annotations

import hashlib
import io
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from PIL import Image

from src.providers import ImageBackend

TARGET_WIDTH = 1200
MAX_BYTES = 200 * 1024
WEBP_QUALITIES = (82, 72, 62, 52)
# 2026-10-13 사용자 지시: 이전 그림(어두운 녹슨 유화 톤)이 칙칙하다 → 밝고 선명한 에디토리얼 일러스트(10-21 아래 스타일로 다시 교체).
# 사람 손으로 그린 질감(과슈·종이결)은 유지하되 색은 채도 높게, 배경은 밝게. 왼쪽 45%는 비워 두고 그 자리에 썸네일 문구를 얹는다 (src/thumbnail.py)
# 2026-10-21 사용자 지시: 풍자 개념 일러스트 스타일(~/Projects/인상적인 이미지/스타일분석.md)로 교체.
# 균일한 검은 윤곽선 + 플랫 색 4~6개 + 배경 단색 하나가 화면 대부분 + 종이 그레인, 작은 무표정 인물 허용.
_BRIGHT_LOOK = (
    'conceptual satirical editorial illustration in ligne claire style, uniform-weight clean black ink outlines, '
    'completely flat color fills, no gradients, no shading except one flat cast shadow, limited palette of 4 to 6 '
    'bright slightly muted print colors (lemon yellow, cobalt blue, clay orange, grass green, tomato red), one dominant '
    'flat background color covering most of the canvas, visible risograph paper grain over the whole image, '
    'generous empty space, only 3 to 6 elements, minimal perspective, calm witty mood, small figures with realistic '
    'proportions, dot eyes and deadpan neutral faces (no smiles), every person has exactly two arms and two hands, '
    'not dark, not gloomy, not 3D, not glossy, not photorealistic, no CGI render look, no neon glow, no decorative '
    'clouds, no text, no letters, no numbers, no logos, no real people'
)
STYLE_SUFFIX = (
    f'{_BRIGHT_LOOK}, one visual irony as the hero, subject in the lower right of a landscape frame, '
    'plain flat uncluttered negative space across the upper-left 60% of the frame'  # 2026-10-22 문구 상자가 왼쪽 위
)
# 본문 중간 그림: 같은 색감, 제목을 얹지 않으므로 가운데 구도
BODY_STYLE_SUFFIX = f'{_BRIGHT_LOOK}, one clear visual irony centered in a landscape frame, simple uncluttered background'
BODY_WIDTH = 1000
BODY_MAX_BYTES = 120 * 1024
CACHE_CONTROL = 'public, max-age=31536000, immutable'
R2_ENV_KEYS = ('R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET', 'R2_PUBLIC_BASE_URL')


class ImageConfigError(Exception):
    """R2 설정이 일부만 채워져 있거나 잘못되었을 때 발생합니다."""


@dataclass(frozen=True)
class R2Config:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    public_base_url: str

    @property
    def endpoint_url(self) -> str:
        return f'https://{self.account_id}.r2.cloudflarestorage.com'


def load_r2_config(env: Mapping[str, str]) -> R2Config | None:
    """R2 값이 하나도 없으면 None(업로드 생략), 일부만 있으면 오류."""
    values = {key: env.get(key, '').strip() for key in R2_ENV_KEYS}
    if not any(values.values()):
        return None
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise ImageConfigError(f'R2 설정이 비어 있습니다: {", ".join(missing)}')
    if not values['R2_PUBLIC_BASE_URL'].startswith('https://'):
        raise ImageConfigError('R2_PUBLIC_BASE_URL은 https://로 시작해야 합니다.')
    return R2Config(
        account_id=values['R2_ACCOUNT_ID'],
        access_key_id=values['R2_ACCESS_KEY_ID'],
        secret_access_key=values['R2_SECRET_ACCESS_KEY'],
        bucket=values['R2_BUCKET'],
        public_base_url=values['R2_PUBLIC_BASE_URL'].rstrip('/'),
    )


def with_style(prompt: str) -> str:
    """LLM이 스타일 문구를 빠뜨려도 항상 같은 톤과 금지 조건이 붙도록 합니다."""
    return prompt if STYLE_SUFFIX in prompt else f'{prompt.rstrip(" .,")}, {STYLE_SUFFIX}'


def with_body_style(prompt: str) -> str:
    return prompt if BODY_STYLE_SUFFIX in prompt else f'{prompt.rstrip(" .,")}, {BODY_STYLE_SUFFIX}'


def generate_body_image(backend: ImageBackend, model: str, quality: str, prompt: str) -> bytes:
    """본문 중간에 넣는 보조 그림. 대표 이미지와 같은 색감이지만 제목 자리를 비우지 않는다."""
    return backend.generate(model, with_body_style(prompt), quality)


def generate_image(backend: ImageBackend, model: str, quality: str, prompt: str) -> bytes:
    """선택한 AI 회사의 이미지 모델로 대표 이미지를 만듭니다. 스타일/금지 조건은 항상 붙입니다."""
    return backend.generate(model, with_style(prompt), quality)


def optimize_image(data: bytes, width: int = TARGET_WIDTH, max_bytes: int = MAX_BYTES) -> bytes:
    """가로 width px로 줄이고, max_bytes 이하가 될 때까지 WebP 품질을 낮춥니다."""
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert('RGB')
    if image.width > width:
        image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
    encoded = b''
    for quality in WEBP_QUALITIES:
        buffer = io.BytesIO()
        image.save(buffer, format='WEBP', quality=quality, method=6)
        encoded = buffer.getvalue()
        if len(encoded) <= max_bytes:
            break
    return encoded


def object_key(publish_date: str, title: str, suffix: str = '') -> str:
    """posts/2026/09/2026-09-26-1a2b3c4d.webp 형식 (본문 그림은 -b1 같은 접미사). 한글 제목 대신 해시로 고유성을 확보합니다."""
    digest = hashlib.sha256(f'{publish_date}:{title}'.encode()).hexdigest()[:8]
    year, month, _ = publish_date.split('-')
    return f'posts/{year}/{month}/{publish_date}-{digest}{suffix}.webp'


def make_s3_client(config: R2Config) -> Any:
    import boto3

    return boto3.client(
        's3',
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        region_name='auto',
    )


def upload_to_r2(s3: Any, config: R2Config, key: str, data: bytes) -> str:
    s3.put_object(Bucket=config.bucket, Key=key, Body=data, ContentType='image/webp', CacheControl=CACHE_CONTROL)
    return f'{config.public_base_url}/{key}'
