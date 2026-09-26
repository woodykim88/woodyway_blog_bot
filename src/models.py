"""파이프라인 전 단계에서 주고받는 데이터 구조.

- dataclass(frozen=True): 코드 안에서만 쓰는 불변 값
- pydantic BaseModel: LLM 구조화 응답(Structured Output) 스키마
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Label = Literal['AI활용', '업무생산성', 'IT트렌드', '툴추천', '커리어']
LABELS: tuple[str, ...] = ('AI활용', '업무생산성', 'IT트렌드', '툴추천', '커리어')


@dataclass(frozen=True)
class Candidate:
    """RSS에서 수집한 주제 후보. summary는 선별 판단용으로만 쓰고 글에 옮기지 않는다."""

    title: str
    link: str  # 피드 항목 주소 (GeekNews 토픽 페이지 또는 뉴스 기사)
    summary: str
    published: datetime
    source: str = 'GeekNews'
    direct: bool = False  # True면 link가 곧 원문 기사, False면 link 페이지에서 원문 링크를 찾는다


@dataclass(frozen=True)
class Article:
    """원문(1차 출처)에서 추출한 본문."""

    candidate: Candidate
    source_url: str
    text: str


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    score: int
    one_line: str


@dataclass(frozen=True)
class ShortNews:
    title: str
    one_line: str
    url: str


# --- LLM 응답 스키마 ---

class CandidateScore(BaseModel):
    index: int = Field(description='후보 목록의 번호')
    score: int = Field(description='직장인 유용도 1~10점')
    reason: str = Field(description='점수 근거 한 문장')
    one_line: str = Field(description='직장인에게 이 소식이 왜 쓸모 있는지 30자 내외 한 줄 (자신의 말로)')
    story: str = Field(description='소식의 주인공인 회사·서비스·제품 이름만 한국어로. 동사·수식어 없이 (예: "마이크로소프트 코파일럿", "엑셀", "메타 뮤즈")')


class SelectionResult(BaseModel):
    scores: list[CandidateScore]


class Section(BaseModel):
    heading: str = Field(description='궁금증을 담은 소제목 (20자 이내)')
    body: str = Field(description='문단 2~3개. 공감 → 원문 사실 → 해결 실마리. 문단 사이는 빈 줄 한 개로 구분')
    tip: str = Field(description='이 섹션에서 독자가 바로 써먹을 꿀팁 한 문장')


class GlossaryItem(BaseModel):
    term: str = Field(description='본문에서 뺀 어려운 용어 (원래 이름 그대로)')
    meaning: str = Field(description='비개발 직장인이 이해할 수 있는 쉬운 설명 1~2문장')


class PostDraft(BaseModel):
    search_keyword: str = Field(description='한국 독자가 검색창에 실제로 입력할 핵심 검색어 (2~4단어)')
    title: str = Field(description='40자 이내. 검색어를 앞쪽에 넣고, 원문에서 가장 의외인 사실로 궁금증을 만든다')
    hook: str = Field(description='시선을 사로잡는 도입부 2~4문장. 독자가 겪는 장면이나 가장 의외인 사실로 시작. 짧은 문단은 빈 줄로 구분')
    key_points: list[str] = Field(description='핵심 내용 불릿 3~5개, 각 1~2문장')
    sections: list[Section] = Field(description='자세히 보기 섹션 2~3개')
    why_it_matters: str = Field(description='직장인 입장에서 왜 중요한지 2~3문장')
    action_items: list[str] = Field(description='내일 바로 해볼 수 있는 구체적 행동 1~3개')
    glossary: list[GlossaryItem] = Field(description='글 하단 용어사전. 본문에서 뺀 어려운 용어 0~6개')
    labels: list[Label] = Field(description='라벨 1~2개')
    meta_description: str = Field(description='검색 결과용 설명 150자 이내')
    image_prompt: str = Field(description='대표 이미지용 영문 프롬프트. 글자, 실존 인물, 로고 없이')
    image_alt: str = Field(description='대표 이미지 대체 텍스트 (한국어)')


ClaimKind = Literal['supported_fact', 'unsupported_fact', 'opinion_or_advice']


class ClaimCheck(BaseModel):
    sentence: str = Field(description='검사한 문장 (짧게 인용)')
    kind: ClaimKind = Field(description='supported_fact / unsupported_fact / opinion_or_advice')
    reason: str = Field(description='판단 근거 한 문장')


class FactCheck(BaseModel):
    claims: list[ClaimCheck] = Field(description='글 속 사실 주장과 의견 문장의 분류 결과')
