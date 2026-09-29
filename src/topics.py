"""글 분야: AI·IT 2 : 생활경제·재테크 1 (사용자 결정 2026-09-30: AI 정보·소식을 늘리되 대중적으로. 그 전에는 반반).

분야는 발행 시각이 아니라 최근 글의 분야 비율로 정한다. 수동 발행이 끼어도 비율이 유지된다.
12:30 스킬 글은 이 계산에서 빠지지만 AI 글이라, 하루 3편 중 AI 관련이 평균 5/6이 된다.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src import collector
from src.history import HistoryEntry

TECH = 'tech'
MONEY = 'money'
SKILL = 'skill'  # Claude Code 스킬 글: 12:30 전용 칸, 뉴스 반반 계산에서 뺀다 (2026-09-30 사용자 결정)
CATEGORIES = (MONEY, TECH)
SKILL_LABEL = 'AI스킬'
BALANCE_WINDOW = 9  # 최근 이만큼의 글에서 분야 비율을 센다 (2:1이 정확히 나눠떨어지는 수)
WEIGHTS = {TECH: 2, MONEY: 1}  # 분야별 목표 비중


@dataclass(frozen=True)
class CategorySpec:
    name: str                                  # 로그·알림에 쓰는 한국어 이름
    sources: tuple[collector.FeedSource, ...]
    themes: tuple[str, ...]                    # 월(0) ~ 일(6) 테마 (맞으면 선별 가점)
    labels: tuple[str, ...]                    # 이 분야 글에 붙일 수 있는 라벨
    selector_prompt: str
    persona_prompt: str


SPECS = {
    TECH: CategorySpec(
        name='AI·IT', sources=collector.DEFAULT_SOURCES,
        themes=('AI활용', '툴추천', 'IT트렌드', '업무생산성', '커리어', '자유 주제', '자유 주제'),
        labels=('AI활용', '업무생산성', 'IT트렌드', '툴추천', '커리어'),
        selector_prompt='selector', persona_prompt='writer_persona_tech',
    ),
    MONEY: CategorySpec(
        name='생활경제·재테크', sources=collector.MONEY_SOURCES,
        themes=('세금·환급', '지원금·혜택', '물가·장보기', '금리·대출·예적금', '부동산·청약', '카드·포인트·통신비', '자유 주제'),
        labels=('재테크', '생활경제', '지원금·혜택', '절약꿀팁', '부동산'),
        selector_prompt='selector_money', persona_prompt='writer_persona_money',
    ),
    SKILL: CategorySpec(
        name='Claude Code 스킬', sources=(),
        themes=('Claude Code 스킬',) * 7,
        labels=(SKILL_LABEL, 'AI활용', '툴추천', '업무생산성'),
        selector_prompt='', persona_prompt='writer_persona_skill',
    ),
}
MONEY_LABELS = frozenset(SPECS[MONEY].labels)


def category_of(entry: HistoryEntry) -> str:
    if SKILL_LABEL in entry.labels:
        return SKILL
    return MONEY if any(label in MONEY_LABELS for label in entry.labels) else TECH


def choose_category(past: Sequence[HistoryEntry]) -> str:
    """최근 BALANCE_WINDOW편에서 목표 비중보다 모자란 분야. 같으면 AI·IT (기록이 없어도 AI·IT부터)."""
    news = [e for e in past if category_of(e) != SKILL]  # 스킬 글은 따로 칸이 있어 비율 계산에서 뺀다
    recent = [category_of(e) for e in news[-BALANCE_WINDOW:]]
    fill = {c: recent.count(c) / WEIGHTS[c] for c in CATEGORIES}
    return MONEY if fill[MONEY] < fill[TECH] else TECH


def other(category: str) -> str:
    return TECH if category == MONEY else MONEY
