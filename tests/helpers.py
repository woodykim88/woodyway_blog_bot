"""테스트 공용 가짜 객체와 샘플 데이터."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.history import HistoryEntry
from src.llm import LLMClient
from src.models import Article, Candidate, GlossaryItem, PostDraft, Section
from src.providers import OpenAITextBackend

FIXTURES = Path(__file__).parent / 'fixtures'
NOW = datetime(2026, 9, 25, 11, 0, tzinfo=timezone.utc)  # 20:00 KST


class FakeOpenAI:
    """schema 이름별로 준비된 응답을 순서대로 돌려주는 가짜 OpenAI 클라이언트."""

    def __init__(self, responses: dict[str, list[Any]]) -> None:
        self._responses = {k: list(v) for k, v in responses.items()}
        self.requests: list[dict[str, Any]] = []
        self.responses = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(kwargs)
        queue = self._responses[kwargs['text_format'].__name__]
        return SimpleNamespace(output_parsed=queue.pop(0))


def fake_llm(responses: dict[str, list[Any]], max_calls: int = 12) -> tuple[LLMClient, FakeOpenAI]:
    client = FakeOpenAI(responses)
    return LLMClient(OpenAITextBackend(client), max_calls=max_calls), client


def make_candidate(i: int = 0, title: str | None = None) -> Candidate:
    return Candidate(
        title=title or f'후보 제목 {i}',
        link=f'https://news.hada.io/topic?id={1000 + i}',
        summary=f'요약 {i}',
        published=NOW,
    )


def make_article(candidate: Candidate | None = None) -> Article:
    return Article(
        candidate=candidate or make_candidate(),
        source_url='https://example.com/original',
        text='원문 본문입니다. ' * 100,
    )


def make_draft(**overrides: Any) -> PostDraft:
    paragraph = '회의록 정리는 많은 직장인이 매일 반복하는 일입니다. ' * 10
    fields: dict[str, Any] = {
        'title': '회의록 AI 요약, 30분 걸리던 정리가 1분이면 끝나는 이유',
        'search_keyword': '회의록 AI 요약',
        'hook': '회의는 30분 만에 끝났는데, 회의록 정리는 왜 한 시간이 걸릴까요?\n\n녹음 파일 하나로 그 시간을 1분으로 줄인 사람들이 있습니다.',
        'key_points': ['첫째 핵심 내용입니다.', '둘째 핵심 내용입니다.', '셋째 핵심 내용입니다.'],
        'sections': [
            Section(heading='배경', body=f'{paragraph}\n\n{paragraph}', tip='회의 시작 전에 녹음 버튼부터 누르세요.'),
            Section(heading='작동 방식', body=f'{paragraph}\n\n{paragraph}', tip='요약 결과는 담당자별로 나눠 달라고 하세요.'),
        ],
        'why_it_matters': '회의가 많은 조직일수록 정리 시간이 누적됩니다. 이를 줄이면 본업에 집중할 수 있습니다.',
        'action_items': ['다음 회의 녹음 파일을 요약 도구에 넣어 보세요.'],
        'glossary': [GlossaryItem(term='STT', meaning='말소리를 글자로 바꿔 주는 기술입니다.')],
        'labels': ['AI활용'],
        'meta_description': '회의록 AI 요약으로 정리 시간을 줄이는 활용법',
        'image_prompt': 'people at a desk, flat illustration, no text',
        'image_alt': '책상에서 회의록을 정리하는 모습',
    }
    return PostDraft(**{**fields, **overrides})


def make_entry(title: str = '이전 글', labels: tuple[str, ...] = ('AI활용',), **overrides: Any) -> HistoryEntry:
    fields: dict[str, Any] = {
        'publish_date': '2026-09-24',
        'post_id': '1',
        'title': title,
        'url': 'https://www.woodyway88.com/2026/09/prev.html',
        'source_url': 'https://example.com/prev',
        'geeknews_url': 'https://news.hada.io/topic?id=1',
        'labels': labels,
        'status': 'SCHEDULED',
    }
    return HistoryEntry(**{**fields, **overrides})
