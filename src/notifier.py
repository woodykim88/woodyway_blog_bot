"""텔레그램 알림. TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID가 없으면 아무것도 하지 않는다."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import requests

TELEGRAM_API = 'https://api.telegram.org'
MAX_MESSAGE_CHARS = 4000  # 텔레그램 한도 4096자


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


def load_telegram_config(env: Mapping[str, str]) -> TelegramConfig | None:
    token = env.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = env.get('TELEGRAM_CHAT_ID', '').strip()
    return TelegramConfig(token, chat_id) if token and chat_id else None


def notify(
    config: TelegramConfig | None,
    text: str,
    post: Callable[..., Any] = requests.post,
) -> bool:
    """메시지를 보냅니다. 알림 실패가 발행을 막으면 안 되므로 예외 대신 False를 반환합니다."""
    if config is None:
        return False
    try:
        response = post(
            f'{TELEGRAM_API}/bot{config.bot_token}/sendMessage',
            json={'chat_id': config.chat_id, 'text': text[:MAX_MESSAGE_CHARS], 'disable_web_page_preview': False},
            timeout=15,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        # URL에 봇 토큰이 들어 있으므로 예외 메시지 전체를 출력하지 않는다
        print(f'[알림 실패] 텔레그램 전송 오류: {e.__class__.__name__}')
        return False
