import base64
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from openai import OpenAIError
from PIL import Image

from src import image, providers, thumbnail
from src import main as pipeline
from tests.helpers import make_article, make_draft

R2_ENV = {
    'R2_ACCOUNT_ID': 'acc123',
    'R2_ACCESS_KEY_ID': 'key',
    'R2_SECRET_ACCESS_KEY': 'secret',
    'R2_BUCKET': 'blog-images',
    'R2_PUBLIC_BASE_URL': 'https://pub-abc.r2.dev/',
}


def noisy_webp(width=1536, height=1024) -> bytes:
    """압축이 잘 안 되는 이미지(용량 줄이기 테스트용)."""
    import random
    rnd = random.Random(0)
    im = Image.frombytes('RGB', (width, height), bytes(rnd.getrandbits(8) for _ in range(width * height * 3)))
    buffer = io.BytesIO()
    im.save(buffer, format='WEBP', quality=95)
    return buffer.getvalue()


def fake_openai(data: bytes):
    client = MagicMock()
    client.images.generate.return_value = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(data).decode())]
    )
    return client


def backend(data: bytes):
    client = fake_openai(data)
    return providers.OpenAIImageBackend(client), client


# --- 설정 ---

def test_load_r2_config_none_when_unset():
    assert image.load_r2_config({}) is None


def test_load_r2_config_full():
    config = image.load_r2_config(R2_ENV)
    assert config.public_base_url == 'https://pub-abc.r2.dev'
    assert config.endpoint_url == 'https://acc123.r2.cloudflarestorage.com'


def test_load_r2_config_partial_raises():
    with pytest.raises(image.ImageConfigError, match='R2_BUCKET'):
        image.load_r2_config({**R2_ENV, 'R2_BUCKET': ''})


def test_load_r2_config_requires_https():
    with pytest.raises(image.ImageConfigError):
        image.load_r2_config({**R2_ENV, 'R2_PUBLIC_BASE_URL': 'http://pub.r2.dev'})


# --- 생성 / 최적화 / 업로드 ---

def test_style_suffix_is_hand_drawn_with_bans():
    """AI 티 나는 광택 3D 대신 사람이 그린 듯한 감성 일러스트 (2026-09-27 지혜 님 요청)."""
    suffix = image.STYLE_SUFFIX
    for ban in ('no text', 'no letters', 'no numbers', 'no logos', 'no real people', 'not 3D', 'not glossy'):
        assert ban in suffix
    for word in ('hand-drawn', 'gouache', 'negative space on the left'):
        assert word in suffix
    assert 'office' not in suffix and 'cinematic' not in suffix


def test_with_style_appends_suffix_once():
    assert image.with_style('a desk.').endswith(image.STYLE_SUFFIX)
    styled = f'a desk, {image.STYLE_SUFFIX}'
    assert image.with_style(styled) == styled


def test_generate_image_requests_webp_landscape():
    image_backend, client = backend(b'webp-bytes')
    assert image.generate_image(image_backend, 'gpt-image-2', 'medium', 'a desk') == b'webp-bytes'
    kwargs = client.images.generate.call_args.kwargs
    assert kwargs['model'] == 'gpt-image-2'
    assert kwargs['size'] == '1536x1024' and kwargs['output_format'] == 'webp'
    assert 'no text' in kwargs['prompt']


def test_optimize_image_resizes_and_limits_size():
    out = image.optimize_image(noisy_webp(), max_bytes=150 * 1024)
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (1200, 800) and im.format == 'WEBP'
    assert len(out) <= 150 * 1024 or len(out) < len(noisy_webp())


def test_object_key_is_stable_and_ascii():
    key = image.object_key('2026-09-26', '회의록 정리 3가지')
    assert key.startswith('posts/2026/09/2026-09-26-') and key.endswith('.webp')
    assert key.isascii()
    assert key == image.object_key('2026-09-26', '회의록 정리 3가지')


def test_upload_to_r2():
    s3 = MagicMock()
    config = image.load_r2_config(R2_ENV)
    url = image.upload_to_r2(s3, config, 'posts/a.webp', b'data')
    assert url == 'https://pub-abc.r2.dev/posts/a.webp'
    s3.put_object.assert_called_once_with(
        Bucket='blog-images', Key='posts/a.webp', Body=b'data',
        ContentType='image/webp', CacheControl=image.CACHE_CONTROL,
    )


# --- 파이프라인 연결 ---

COMPOSED = pipeline.Composed(make_article(), make_draft())


def test_attach_hero_image_dry_run_saves_preview_file(tmp_path):
    result = pipeline.attach_hero_image(
        COMPOSED, backend(noisy_webp(300, 200))[0], 'gpt-image-2', 'medium', None, '2026-09-26', True,
        preview_dir=tmp_path,
    )
    assert result.hero_image_url == 'hero.webp'
    assert (tmp_path / 'hero.webp').exists()
    assert '<img src="hero.webp"' in result.html
    assert COMPOSED.hero_image_url is None  # 원본은 그대로


def test_attach_hero_image_uploads_to_r2():
    s3 = MagicMock()
    result = pipeline.attach_hero_image(
        COMPOSED, backend(noisy_webp(300, 200))[0], 'gpt-image-2', 'medium', image.load_r2_config(R2_ENV),
        '2026-09-26', False, make_s3=lambda cfg: s3,
    )
    assert result.hero_image_url.startswith('https://pub-abc.r2.dev/posts/2026/09/2026-09-26-')
    assert s3.put_object.called


def test_attach_hero_image_skips_without_r2_when_publishing():
    image_backend, client = backend(b'')
    assert pipeline.attach_hero_image(
        COMPOSED, image_backend, 'gpt-image-2', 'medium', None, '2026-09-26', False
    ) is COMPOSED
    client.images.generate.assert_not_called()


def test_attach_hero_image_failure_continues_without_image():
    client = MagicMock()
    client.images.generate.side_effect = OpenAIError('rate limited')
    result = pipeline.attach_hero_image(
        COMPOSED, providers.OpenAIImageBackend(client), 'gpt-image-2', 'medium', image.load_r2_config(R2_ENV),
        '2026-09-26', False, make_s3=lambda c: MagicMock(),
    )
    assert result is COMPOSED


def captured_headlines():
    """attach_hero_image가 add_headline에 넘긴 값을 기록하는 가짜 함수."""
    calls = []

    def fake(data, headline, font):
        calls.append((headline, font))
        return data
    return calls, fake


def test_attach_hero_image_overlays_thumbnail_text(tmp_path, monkeypatch):
    calls, fake = captured_headlines()
    monkeypatch.setattr(thumbnail, 'add_headline', fake)
    font = thumbnail.FontSpec('/fonts/x.ttc', 1)
    pipeline.attach_hero_image(
        COMPOSED, backend(noisy_webp(300, 200))[0], 'gpt-image-2', 'medium', None, '2026-09-26', True,
        preview_dir=tmp_path, find_font=lambda: font,
    )
    headline, used_font = calls[0]
    draft = COMPOSED.draft
    assert headline == thumbnail.Headline(draft.thumbnail_text, draft.thumbnail_highlight, draft.labels[0])
    assert used_font == font


def test_attach_hero_image_without_font_still_attaches_image(tmp_path):
    result = pipeline.attach_hero_image(
        COMPOSED, backend(noisy_webp(300, 200))[0], 'gpt-image-2', 'medium', None, '2026-09-26', True,
        preview_dir=tmp_path, find_font=lambda: None,
    )
    assert result.hero_image_url == 'hero.webp'
