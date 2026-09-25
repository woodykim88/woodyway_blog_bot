from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import providers, runlog, schedule
from src.models import FactCheck

KST = schedule.KST


# --- AI 설정 ---

def test_load_ai_settings_defaults_to_openai():
    s = providers.load_ai_settings({})
    assert (s.text_provider, s.writer_model, s.fast_model) == ('openai', 'gpt-5.5', 'gpt-5.4-mini')
    assert (s.image_provider, s.image_model, s.image_quality) == ('openai', 'gpt-image-2', 'medium')


def test_load_ai_settings_legacy_openai_names():
    s = providers.load_ai_settings({'OPENAI_MODEL_WRITER': 'gpt-5.6-sol', 'OPENAI_IMAGE_QUALITY': 'high'})
    assert s.writer_model == 'gpt-5.6-sol' and s.image_quality == 'high'


def test_load_ai_settings_switch_provider_uses_that_providers_defaults():
    s = providers.load_ai_settings({'TEXT_PROVIDER': 'anthropic', 'OPENAI_MODEL_WRITER': 'gpt-5.5',
                                    'IMAGE_PROVIDER': 'gemini'})
    assert s.writer_model == providers.DEFAULT_TEXT_MODELS['anthropic']['writer']  # 예전 OpenAI 이름 무시
    assert s.image_model == providers.DEFAULT_IMAGE_MODELS['gemini']


def test_load_ai_settings_explicit_models():
    s = providers.load_ai_settings({'TEXT_PROVIDER': 'Gemini', 'WRITER_MODEL': 'gemini-x', 'FAST_MODEL': 'gemini-y'})
    assert (s.text_provider, s.writer_model, s.fast_model) == ('gemini', 'gemini-x', 'gemini-y')


@pytest.mark.parametrize('env', [
    {'TEXT_PROVIDER': 'mistral'},
    {'IMAGE_PROVIDER': 'anthropic'},  # Claude는 이미지 생성 미지원
    {'IMAGE_QUALITY': 'ultra'},
])
def test_load_ai_settings_rejects_invalid(env):
    with pytest.raises(providers.ProviderConfigError):
        providers.load_ai_settings(env)


def test_api_key_required():
    with pytest.raises(providers.ProviderConfigError, match='ANTHROPIC_API_KEY'):
        providers.api_key({}, 'anthropic')
    assert providers.api_key({'GEMINI_API_KEY': ' k '}, 'gemini') == 'k'


# --- 텍스트 백엔드 ---

OK = FactCheck(claims=[])


def test_openai_text_backend():
    client = MagicMock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=OK)
    assert providers.OpenAITextBackend(client).parse('m', 'sys', 'user', FactCheck) is OK
    kwargs = client.responses.parse.call_args.kwargs
    assert kwargs == {'model': 'm', 'instructions': 'sys', 'input': 'user', 'text_format': FactCheck}


def test_anthropic_text_backend():
    client = MagicMock()
    client.messages.parse.return_value = SimpleNamespace(parsed_output=OK)
    assert providers.AnthropicTextBackend(client).parse('claude-x', 'sys', 'user', FactCheck) is OK
    kwargs = client.messages.parse.call_args.kwargs
    assert kwargs['system'] == 'sys' and kwargs['output_format'] is FactCheck
    assert kwargs['messages'] == [{'role': 'user', 'content': 'user'}]


def test_gemini_text_backend_returns_model_or_validates_dict():
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(parsed=OK)
    assert providers.GeminiTextBackend(client).parse('gemini-x', 'sys', 'user', FactCheck) is OK
    config = client.models.generate_content.call_args.kwargs['config']
    assert config.system_instruction == 'sys' and config.response_mime_type == 'application/json'

    client.models.generate_content.return_value = SimpleNamespace(parsed={'claims': []})
    assert providers.GeminiTextBackend(client).parse('gemini-x', 'sys', 'user', FactCheck) == OK


# --- 이미지 백엔드 ---

def test_gemini_image_backend_imagen():
    client = MagicMock()
    client.models.generate_images.return_value = SimpleNamespace(
        generated_images=[SimpleNamespace(image=SimpleNamespace(image_bytes=b'png'))]
    )
    assert providers.GeminiImageBackend(client).generate('imagen-4.0-generate-001', 'p', 'medium') == b'png'
    assert client.models.generate_images.call_args.kwargs['config'].aspect_ratio == '16:9'


def test_gemini_image_backend_imagen_empty_raises():
    client = MagicMock()
    client.models.generate_images.return_value = SimpleNamespace(generated_images=[])
    with pytest.raises(ValueError):
        providers.GeminiImageBackend(client).generate('imagen-4', 'p', 'medium')


def test_gemini_image_backend_flash_image():
    part = SimpleNamespace(inline_data=SimpleNamespace(data=b'img'))
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(inline_data=None), part]))]
    )
    assert providers.GeminiImageBackend(client).generate('gemini-2.5-flash-image', 'p', 'medium') == b'img'


# --- 발행 시각 ---

def test_parse_times():
    assert schedule.parse_times(None) == ((20, 0),)
    assert schedule.parse_times(' 20:00, 08:30,20:00 ') == ((8, 30), (20, 0))
    assert schedule.format_times(((8, 30), (20, 0))) == '08:30,20:00'


@pytest.mark.parametrize('value', ['8시', '24:00', '12:60', '08:00,12:00,18:00,21:00', ' , '])
def test_parse_times_rejects_invalid(value):
    with pytest.raises(schedule.ScheduleError):
        schedule.parse_times(value)


def test_due_slots_within_lead_window():
    times = ((8, 0), (20, 0))
    now = datetime(2026, 9, 27, 15, 0, tzinfo=KST)
    assert [schedule.slot_key(s) for s in schedule.due_slots(now, times, set())] == ['2026-09-27T20:00']
    assert schedule.due_slots(now, times, {'2026-09-27T20:00'}) == []


def test_due_slots_crosses_midnight_and_respects_min_lead():
    times = ((1, 0), (19, 55))
    now = datetime(2026, 9, 27, 19, 50, tzinfo=KST)  # 19:55는 5분 뒤라 너무 늦음
    assert [schedule.slot_key(s) for s in schedule.due_slots(now, times, set())] == ['2026-09-28T01:00']


def test_next_open_slot_skips_done():
    times = ((8, 0), (20, 0))
    now = datetime(2026, 9, 27, 7, 0, tzinfo=KST)
    assert schedule.slot_key(schedule.next_open_slot(now, times, {'2026-09-27T08:00'})) == '2026-09-27T20:00'


def test_upcoming_slots_requires_aware_datetime():
    with pytest.raises(ValueError):
        schedule.upcoming_slots(datetime(2026, 9, 27), ((20, 0),))  # noqa: DTZ001


# --- 실행 기록 ---

def test_runlog_append_and_parse(tmp_path):
    path = tmp_path / 'runs.jsonl'
    post = runlog.PostRecord('제목', 'SCHEDULED', '2026-09-27T20:00', 'https://w/x', 'https://e', True)
    runlog.append_run(runlog.RunRecord('s', 'f', 'schedule', 'auto', 'success', 'ok', posts=(post,)), path)
    runlog.append_run(runlog.RunRecord('s2', 'f2', 'local', 'preview', 'failed', 'x' * 900), path)
    path.write_text(path.read_text() + '{broken\n')
    rows = runlog.parse_runs(path.read_text())
    assert [r['mode'] for r in rows] == ['preview', 'auto']  # 최신순, 깨진 줄 무시
    assert len(rows[0]['message']) == runlog.MAX_ERROR_CHARS
    assert rows[1]['posts'][0]['title'] == '제목'


def test_github_context():
    ctx = runlog.github_context({'GITHUB_RUN_ID': '9', 'GITHUB_SERVER_URL': 'https://github.com',
                                 'GITHUB_REPOSITORY': 'o/r', 'GITHUB_EVENT_NAME': 'schedule'})
    assert ctx == {'trigger': 'schedule', 'run_id': '9', 'run_url': 'https://github.com/o/r/actions/runs/9'}
    assert runlog.github_context({})['trigger'] == 'local'
