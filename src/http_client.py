"""외부 웹 요청 공통 함수.

- 봇임을 밝히는 User-Agent를 사용한다.
- 원문 링크는 제3자가 올린 값이므로, 사설/루프백/링크로컬(클라우드 메타데이터) 주소로의
  요청을 막는다. 리디렉트도 자동으로 따라가지 않고 매 단계 주소를 다시 검사한다.
"""
from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

import requests

USER_AGENT = 'WoodywayBlogBot/1.0 (+https://www.woodyway88.com)'
TIMEOUT_SECONDS = 20
MAX_REDIRECTS = 5

HttpGet = Callable[[str], str]
Resolver = Callable[[str], list[str]]


class UnsafeUrlError(requests.RequestException):
    """공인 인터넷 주소가 아닌 곳으로 요청하려 할 때 발생합니다."""


def resolve_host(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def is_public_url(url: str, resolve: Resolver = resolve_host) -> bool:
    """http(s)이고, 호스트가 가리키는 모든 IP가 공인 주소일 때만 True."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return False
    try:
        addresses = resolve(parsed.hostname)
    except OSError:
        return False
    return bool(addresses) and all(ipaddress.ip_address(a.split('%')[0]).is_global for a in addresses)


def _get(url: str, resolve: Resolver, stream: bool = False) -> requests.Response:
    """리디렉트를 직접 따라가며 매 단계 주소를 검사합니다."""
    for _ in range(MAX_REDIRECTS + 1):
        if not is_public_url(url, resolve):
            raise UnsafeUrlError(f'공인 주소가 아니어서 요청하지 않습니다: {url}')
        response = requests.get(
            url, headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT_SECONDS,
            allow_redirects=False, stream=stream,
        )
        if not response.is_redirect:
            return response
        response.close()
        url = urljoin(url, response.headers['location'])
    raise requests.TooManyRedirects(f'리디렉트가 {MAX_REDIRECTS}회를 넘었습니다: {url}')


def http_get(url: str, resolve: Resolver = resolve_host) -> str:
    """URL의 본문을 문자열로 반환합니다. 4xx/5xx면 requests.HTTPError를 올립니다."""
    response = _get(url, resolve)
    response.raise_for_status()
    return response.text


def url_is_alive(url: str, resolve: Resolver = resolve_host) -> bool:
    """링크가 살아 있는지(최종 응답 4xx/5xx가 아님) 확인합니다."""
    try:
        response = _get(url, resolve, stream=True)
        response.close()
        return response.status_code < 400
    except requests.RequestException:
        return False
