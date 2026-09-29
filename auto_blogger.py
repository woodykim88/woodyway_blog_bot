"""Blogger API v3로 글을 초안 저장, 예약 발행, 즉시 발행하는 모듈.

사용법:
    python auto_blogger.py                  # 샘플 글을 초안으로 저장 (기본, 가장 안전)
    python auto_blogger.py --mode schedule  # 샘플 글을 다음 PUBLISH_HOUR(기본 20시 KST)에 예약 발행
    python auto_blogger.py --mode now       # 샘플 글을 즉시 공개 발행
    python auto_blogger.py --mode schedule --post-id <초안ID>  # 이미 저장된 초안을 예약 발행
    python auto_blogger.py --mode schedule --at 21:20          # 원하는 시각(KST)에 예약
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Blogger 쓰기/수정 권한 스코프
SCOPES = ['https://www.googleapis.com/auth/blogger']
TOKEN_URI = 'https://oauth2.googleapis.com/token'

KST = ZoneInfo('Asia/Seoul')
UTC = timezone.utc
DEFAULT_PUBLISH_HOUR = 20

# 일시적 오류로 보고 재시도할 HTTP 상태 코드
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class ConfigError(Exception):
    """환경변수 설정이 잘못되었을 때 발생합니다."""


@dataclass(frozen=True)
class Config:
    blog_id: str
    publish_hour: int
    client_secret_file: str
    token_file: str
    client_id: str
    client_secret: str
    refresh_token: str

    @property
    def is_server_mode(self) -> bool:
        """refresh token이 환경변수로 주어지면 브라우저 없이 동작하는 서버 모드입니다."""
        return bool(self.refresh_token)


def load_config(env: Mapping[str, str]) -> Config:
    """환경변수 딕셔너리에서 설정을 읽고 검증합니다."""
    blog_id = env.get('BLOG_ID', '').strip()
    if not blog_id.isdigit():
        raise ConfigError('BLOG_ID가 비어 있거나 숫자가 아닙니다. .env 파일을 확인하세요.')

    raw_hour = env.get('PUBLISH_HOUR', str(DEFAULT_PUBLISH_HOUR)).strip()
    if not raw_hour.isdigit() or not 0 <= int(raw_hour) <= 23:
        raise ConfigError(f'PUBLISH_HOUR는 0~23 사이 숫자여야 합니다. (현재: {raw_hour!r})')

    config = Config(
        blog_id=blog_id,
        publish_hour=int(raw_hour),
        client_secret_file=env.get('GOOGLE_CLIENT_SECRET_FILE', 'client_secret.json'),
        token_file=env.get('GOOGLE_TOKEN_FILE', 'token.json'),
        client_id=env.get('GOOGLE_CLIENT_ID', '').strip(),
        client_secret=env.get('GOOGLE_CLIENT_SECRET', '').strip(),
        refresh_token=env.get('GOOGLE_REFRESH_TOKEN', '').strip(),
    )
    if config.is_server_mode and not (config.client_id and config.client_secret):
        raise ConfigError('서버 모드에는 GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN이 모두 필요합니다.')
    return config


def next_publish_time(now: datetime, hour: int, minute: int = 0) -> datetime:
    """now 이후 가장 가까운 KST 기준 hour:minute 시각을 반환합니다."""
    if now.tzinfo is None:
        raise ValueError('now에는 시간대 정보가 있어야 합니다.')
    now_kst = now.astimezone(KST)
    target = now_kst.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > now_kst else target + timedelta(days=1)


def parse_hhmm(value: str) -> tuple[int, int]:
    """'21:20' 형식의 문자열을 (21, 20)으로 바꿉니다."""
    parts = value.split(':')
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f'시각은 HH:MM 형식이어야 합니다. (현재: {value!r})')
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f'올바르지 않은 시각입니다. (현재: {value!r})')
    return hour, minute


def build_post_body(title: str, content_html: str, labels: Sequence[str] | None = None) -> dict[str, Any]:
    """Blogger API에 보낼 포스트 본문을 만듭니다."""
    if not title or not title.strip():
        raise ValueError('제목이 비어 있습니다.')
    if not content_html or not content_html.strip():
        raise ValueError('본문이 비어 있습니다.')
    return {
        'kind': 'blogger#post',
        'title': title.strip(),
        'content': content_html,
        'labels': list(labels or []),
    }


def _load_local_credentials(config: Config) -> Credentials:
    """token.json을 사용하고, 없거나 갱신에 실패하면 브라우저 로그인을 진행합니다."""
    creds = None
    if os.path.exists(config.token_file):
        creds = Credentials.from_authorized_user_file(config.token_file, SCOPES)
    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            print('[안내] 저장된 토큰이 만료/해제되어 다시 로그인합니다.')
            creds = None
    else:
        creds = None

    if creds is None:
        if not os.path.exists(config.client_secret_file):
            raise ConfigError(f'{config.client_secret_file} 파일이 없습니다.')
        flow = InstalledAppFlow.from_client_secrets_file(config.client_secret_file, SCOPES)
        creds = flow.run_local_server(port=0)

    # 다음 실행을 위해 토큰 저장 (본인만 읽을 수 있도록 권한 600)
    with open(config.token_file, 'w') as token:
        token.write(creds.to_json())
    os.chmod(config.token_file, 0o600)
    return creds


def get_credentials(config: Config) -> Credentials:
    """서버 모드면 환경변수의 refresh token으로, 아니면 token.json으로 인증합니다."""
    if not config.is_server_mode:
        return _load_local_credentials(config)

    creds = Credentials(
        token=None,
        refresh_token=config.refresh_token,
        token_uri=TOKEN_URI,
        client_id=config.client_id,
        client_secret=config.client_secret,
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
    except RefreshError as e:
        raise ConfigError(f'refresh token 갱신 실패: 로컬에서 다시 로그인해 토큰을 재발급하세요. ({e})') from e
    return creds


def get_blogger_service(config: Config) -> Any:
    """인증된 Blogger 서비스 객체를 반환합니다."""
    return build('blogger', 'v3', credentials=get_credentials(config), cache_discovery=False)


def execute_with_retry(request: Any, max_attempts: int = 3, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """일시적 오류(429, 5xx)는 2초, 4초... 간격으로 재시도하고, 그 외 오류는 바로 올립니다."""
    for attempt in range(1, max_attempts + 1):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status not in RETRYABLE_STATUSES or attempt == max_attempts:
                raise
            delay = 2 ** attempt
            print(f'[재시도] HTTP {e.resp.status}, {delay}초 후 다시 시도합니다. ({attempt}/{max_attempts})')
            sleep(delay)
    raise AssertionError('unreachable')


def create_draft(service: Any, blog_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """글을 초안으로 저장합니다."""
    return execute_with_retry(service.posts().insert(blogId=blog_id, body=body, isDraft=True))


def publish_post(service: Any, blog_id: str, post_id: str, publish_at: datetime | None = None) -> dict[str, Any]:
    """초안을 발행합니다. publish_at이 미래 시각이면 그 시각으로 예약됩니다."""
    params = {'blogId': blog_id, 'postId': post_id}
    if publish_at is not None:
        params['publishDate'] = publish_at.astimezone(KST).isoformat()
    return execute_with_retry(service.posts().publish(**params))


def post_to_blogger(
    service: Any,
    config: Config,
    title: str,
    content_html: str,
    labels: Sequence[str] | None = None,
    mode: str = 'draft',
    now: datetime | None = None,
    at: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """초안 저장 후 mode에 따라 그대로 두거나(draft), 예약(schedule), 즉시 발행(now)합니다."""
    if mode not in ('draft', 'schedule', 'now'):
        raise ValueError(f'알 수 없는 mode: {mode}')
    body = build_post_body(title, content_html, labels)
    draft = create_draft(service, config.blog_id, body)
    if mode == 'draft':
        return draft
    return publish_existing(service, config, draft['id'], mode, now, at)


def publish_existing(
    service: Any,
    config: Config,
    post_id: str,
    mode: str,
    now: datetime | None = None,
    at: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """이미 저장된 초안을 예약(schedule)하거나 즉시 발행(now)합니다.

    at=(시, 분)을 주면 그 시각에, 없으면 설정의 PUBLISH_HOUR 정각에 예약합니다.
    """
    if mode == 'schedule':
        hour, minute = at or (config.publish_hour, 0)
        publish_at = next_publish_time(now or datetime.now(UTC), hour, minute)
        return publish_post(service, config.blog_id, post_id, publish_at)
    if mode == 'now':
        return publish_post(service, config.blog_id, post_id)
    raise ValueError(f'기존 초안에는 schedule 또는 now만 쓸 수 있습니다. (현재: {mode})')


SLUG_MIN_WORDS = 2
SLUG_MAX_WORDS = 6


def slug_title(slug: str) -> str | None:
    """영문 슬러그를 글 주소용 임시 제목으로 바꿉니다. 영어 단어가 2개 미만이면 None.

    Blogger는 처음 발행할 때의 제목으로 글 주소를 정하고, 한글은 주소에서 빠집니다.
    그래서 영문 제목으로 발행해 주소를 정한 뒤 rename_post로 한글 제목을 되돌립니다.
    """
    words = re.findall(r'[a-z0-9]+', slug.lower())
    if len(words) < SLUG_MIN_WORDS or re.search(r'[^\x00-\x7f]', slug):
        return None
    return ' '.join(words[:SLUG_MAX_WORDS])


def rename_post(service: Any, blog_id: str, post_id: str, title: str) -> dict[str, Any]:
    """글 제목만 바꿉니다. 이미 정해진 글 주소는 그대로 유지됩니다."""
    return execute_with_retry(service.posts().patch(blogId=blog_id, postId=post_id, body={'title': title}))


def edit_url(blog_id: str, post_id: str) -> str:
    """Blogger 관리 화면의 글 편집 링크를 반환합니다."""
    return f'https://www.blogger.com/blog/post/edit/{blog_id}/{post_id}'


# 테스트용 샘플 글
SAMPLE_TITLE = "AI 에이전트로 일하는 법: 업무 시간을 1/3로 줄이는 실전 팁"
SAMPLE_CONTENT = """
<p>반복되는 문서 작업과 정보 정리에 지치셨나요? 이제는 AI에게 질문만 던지는 단계를 넘어 직접 워크플로우를 처리하도록 지시할 때입니다.</p>

<h3>1. 회의록 액션 아이템 추출</h3>
<p>녹음 스크립트를 넣고 담당자와 기한 위주로 요약하도록 템플릿을 지정해 보세요. 30분 걸리던 정리가 1분 만에 끝납니다.</p>

<h3>2. 긴 PDF 보고서 3분 분석</h3>
<p>핵심 의사결정에 필요한 데이터 3가지만 발췌하도록 프롬프트를 구성하면 탐독 시간을 대폭 줄일 수 있습니다.</p>

<p>작은 반복 작업부터 자동화해 나가면서 하루 1시간의 여유를 확보해 보세요.</p>
"""
SAMPLE_LABELS = ["IT소식", "유용한앱"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Blogger 샘플 글 발행 테스트')
    parser.add_argument('--mode', choices=['draft', 'schedule', 'now'], default='draft')
    parser.add_argument('--post-id', help='새 글 대신 이미 저장된 초안을 예약/발행할 때 초안 ID')
    parser.add_argument('--at', help='예약 시각 HH:MM (KST). 없으면 PUBLISH_HOUR 정각')
    args = parser.parse_args(argv)
    if args.post_id and args.mode == 'draft':
        parser.error('--post-id는 --mode schedule 또는 --mode now와 함께 써야 합니다.')
    if args.at and args.mode != 'schedule':
        parser.error('--at은 --mode schedule과 함께 써야 합니다.')
    try:
        at = parse_hhmm(args.at) if args.at else None
    except ValueError as e:
        parser.error(str(e))

    load_dotenv()
    try:
        config = load_config(os.environ)
        service = get_blogger_service(config)
        if args.post_id:
            result = publish_existing(service, config, args.post_id, args.mode, at=at)
        else:
            result = post_to_blogger(
                service, config, SAMPLE_TITLE, SAMPLE_CONTENT, SAMPLE_LABELS, args.mode, at=at
            )
    except ConfigError as e:
        print(f'[설정 오류] {e}')
        return 1
    except HttpError as e:
        print(f'[API 오류] HTTP {e.resp.status}: {e.reason}')
        if e.resp.status == 403:
            print('  → 로그인한 Google 계정이 이 블로그의 관리자인지 확인하세요.')
        elif e.resp.status == 404:
            print('  → BLOG_ID 또는 초안 ID가 올바른지 확인하세요.')
        return 1

    print(f"[성공] mode={args.mode}, status={result.get('status')}")
    print(f"글 제목: {result.get('title')}")
    print(f"글 ID: {result.get('id')}")
    print(f"편집 화면: {edit_url(config.blog_id, result.get('id'))}")
    if args.mode == 'schedule':
        print(f"예약 시각: {result.get('published')}")
    if args.mode != 'draft' and result.get('url'):
        print(f"공개 URL: {result.get('url')}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
