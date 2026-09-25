"""관리 화면 ↔ GitHub 통신. 인증은 `gh auth login`으로 저장된 토큰을 그대로 쓴다.

- 설정값(발행 시각, AI 모델 등): 저장소 Variables
- 비밀값(API 키): 저장소 Secrets (gh CLI가 암호화해서 등록)
- 실행: workflow_dispatch / 기록: data/history.json, data/runs.jsonl
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import requests

API = 'https://api.github.com'
DEFAULT_REPO = 'woodykim88/woodyway_blog_bot'
WORKFLOW_FILE = 'daily-post.yml'
TIMEOUT = 20


class GitHubError(Exception):
    """GitHub 요청 실패. 사용자에게 보여줄 한국어 메시지를 담는다."""


def gh_token() -> str:
    try:
        result = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True, timeout=10, check=False)
    except FileNotFoundError as e:
        raise GitHubError('GitHub CLI(gh)가 설치되어 있지 않습니다.') from e
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise GitHubError('GitHub에 로그인되어 있지 않습니다. 터미널에서 `gh auth login`을 실행하세요.')
    return token


class GitHubClient:
    def __init__(self, repo: str, token: str, session: Any = requests) -> None:
        self.repo = repo
        self._token = token
        self._session = session

    # --- 공통 ---

    def _request(self, method: str, path: str, raw: bool = False, **kwargs: Any) -> Any:
        headers = {
            'Authorization': f'Bearer {self._token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            **kwargs.pop('headers', {}),
        }
        try:
            response = self._session.request(method, f'{API}{path}', headers=headers, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as e:
            raise GitHubError(f'GitHub에 연결하지 못했습니다: {e.__class__.__name__}') from e
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise GitHubError(f'GitHub 요청 실패 (HTTP {response.status_code}): {path}')
        if raw:
            return response.text
        if response.status_code == 204 or not response.content:
            return {}
        if 'json' in response.headers.get('content-type', ''):
            return response.json()
        return response.text

    @property
    def _base(self) -> str:
        return f'/repos/{self.repo}'

    # --- 설정값 (Variables) ---

    def list_variables(self) -> dict[str, str]:
        data = self._request('GET', f'{self._base}/actions/variables', params={'per_page': 100}) or {}
        return {v['name']: v['value'] for v in data.get('variables', [])}

    def set_variable(self, name: str, value: str) -> None:
        exists = self._request('GET', f'{self._base}/actions/variables/{name}') is not None
        if exists:
            self._request('PATCH', f'{self._base}/actions/variables/{name}', json={'name': name, 'value': value})
        else:
            self._request('POST', f'{self._base}/actions/variables', json={'name': name, 'value': value})

    def set_variables(self, values: dict[str, str]) -> None:
        current = self.list_variables()
        for name, value in values.items():
            if current.get(name) != value:
                self.set_variable(name, value)

    # --- 비밀값 (Secrets) ---

    def list_secrets(self) -> dict[str, str]:
        """이름 → 마지막 수정 시각. 값은 GitHub도 돌려주지 않는다."""
        data = self._request('GET', f'{self._base}/actions/secrets', params={'per_page': 100}) or {}
        return {s['name']: s.get('updated_at', '') for s in data.get('secrets', [])}

    def set_secret(self, name: str, value: str) -> None:
        """값은 표준 입력으로만 전달해 명령줄·로그에 남지 않게 한다."""
        result = subprocess.run(
            ['gh', 'secret', 'set', name, '--repo', self.repo],
            input=value, text=True, capture_output=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            raise GitHubError(f'{name} 등록에 실패했습니다.')

    # --- 실행 ---

    def dispatch(self, mode: str, at: str | None = None) -> None:
        inputs = {'mode': mode, **({'at': at} if at else {})}
        self._request(
            'POST', f'{self._base}/actions/workflows/{WORKFLOW_FILE}/dispatches',
            json={'ref': 'main', 'inputs': inputs},
        )

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        data = self._request(
            'GET', f'{self._base}/actions/workflows/{WORKFLOW_FILE}/runs', params={'per_page': limit}
        ) or {}
        return [
            {
                'id': r['id'],
                'event': r['event'],
                'status': r['status'],
                'conclusion': r.get('conclusion'),
                'created_at': r['created_at'],
                'url': r['html_url'],
            }
            for r in data.get('workflow_runs', [])
        ]

    def run_log(self, run_id: int) -> str:
        result = subprocess.run(
            ['gh', 'run', 'view', str(run_id), '--repo', self.repo, '--log'],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if result.returncode != 0:
            raise GitHubError('실행 로그를 가져오지 못했습니다 (실행 중이거나 기간이 지난 기록일 수 있습니다).')
        return result.stdout

    def download_preview(self, run_id: int, dest: Path) -> Path | None:
        dest.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ['gh', 'run', 'download', str(run_id), '--repo', self.repo, '-n', 'preview', '-D', str(dest)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        page = dest / 'preview.html'
        return page if result.returncode == 0 and page.exists() else None

    # --- 기록 파일 ---

    def read_file(self, path: str) -> str | None:
        """파일 원문을 그대로 반환합니다 (JSON 파일도 해석하지 않음)."""
        return self._request(
            'GET', f'{self._base}/contents/{path}', raw=True,
            headers={'Accept': 'application/vnd.github.raw+json'},
        )


def key_log_lines(log: str) -> list[str]:
    """전체 로그에서 파이프라인 진행 줄([수집], [검수 통과] 등)만 뽑습니다."""
    lines = []
    for raw in log.splitlines():
        text = raw.split('Z ', 1)[1] if 'Z ' in raw[:80] else raw
        if text.startswith('[') and not text.startswith('[command]'):
            lines.append(text)
    return lines
