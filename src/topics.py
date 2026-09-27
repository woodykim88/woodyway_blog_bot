"""글 분야: AI·IT와 생활경제·재테크를 반반씩 쓴다 (사용자 결정 2026-09-27: 블로그의 핵심은 최대한 많은 클릭).

분야는 발행 시각이 아니라 최근 글의 분야 비율로 정한다. 수동 발행이 끼어도 반반이 유지된다.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src import collector
from src.history import HistoryEntry

TECH = 'tech'
MONEY = 'money'
CATEGORIES = (MONEY, TECH)
BALANCE_WINDOW = 10  # 최근 이만큼의 글에서 분야 비율을 센다


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
}
MONEY_LABELS = frozenset(SPECS[MONEY].labels)


def category_of(entry: HistoryEntry) -> str:
    return MONEY if any(label in MONEY_LABELS for label in entry.labels) else TECH


def choose_category(past: Sequence[HistoryEntry]) -> str:
    """최근 BALANCE_WINDOW편 중 적은 분야. 같으면 직전 글과 다른 분야, 기록이 없으면 생활경제부터."""
    recent = [category_of(e) for e in past[-BALANCE_WINDOW:]]
    if not recent:
        return MONEY
    money, tech = recent.count(MONEY), recent.count(TECH)
    if money != tech:
        return MONEY if money < tech else TECH
    return TECH if recent[-1] == MONEY else MONEY


def other(category: str) -> str:
    return TECH if category == MONEY else MONEY
