"""발행 이력(data/history.json). 중복 주제 방지와 중복 발행 방지(멱등성)에 쓴다."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_HISTORY_PATH = Path(__file__).resolve().parent.parent / 'data' / 'history.json'
LEGACY_SLOT_TIME = '20:00'  # 슬롯 도입 전 기록은 20시 발행분으로 간주


@dataclass(frozen=True)
class HistoryEntry:
    publish_date: str  # YYYY-MM-DD (KST)
    post_id: str
    title: str
    url: str
    source_url: str
    geeknews_url: str  # 후보 링크(GeekNews 토픽 또는 뉴스 기사). 기존 기록과 호환하려고 이름 유지
    labels: tuple[str, ...]
    status: str
    slot: str = ''  # 예약 슬롯 "YYYY-MM-DDTHH:MM" 또는 수동 실행 키 "manual-..."


def load_history(path: Path | None = None) -> tuple[HistoryEntry, ...]:
    path = path or DEFAULT_HISTORY_PATH
    if not path.exists():
        return ()
    rows = json.loads(path.read_text(encoding='utf-8'))
    return tuple(
        HistoryEntry(**{
            **row,
            'labels': tuple(row.get('labels', ())),
            'slot': row.get('slot') or f"{row['publish_date']}T{LEGACY_SLOT_TIME}",
        })
        for row in rows
    )


def append_entry(history: tuple[HistoryEntry, ...], entry: HistoryEntry) -> tuple[HistoryEntry, ...]:
    return (*history, entry)


def save_history(history: tuple[HistoryEntry, ...], path: Path | None = None) -> None:
    path = path or DEFAULT_HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{**asdict(e), 'labels': list(e.labels)} for e in history]
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def has_entry_for(history: tuple[HistoryEntry, ...], publish_date: str) -> bool:
    return any(e.publish_date == publish_date for e in history)


def done_slots(history: tuple[HistoryEntry, ...]) -> set[str]:
    return {e.slot for e in history if e.slot}


# 제목 비교에서 뺄 흔한 말 (주제가 아니라 형식을 나타내는 말)
_GENERIC_WORDS = frozenset({
    '방법', '사용법', '정리', '총정리', '한번에', '이유', '조건', '기준', '확인', '신청', '계산', '무엇', '어떻게',
    '지금', '오늘', '이번', '올해', '내년', '바뀌는', '달라지는', '것', '때', '수', '있나', '없나', '까지', '부터',
})


def topic_words(text: str) -> set[str]:
    """제목·검색어의 주제 낱말 (두 글자 이상, 흔한 형식 말 제외)."""
    return {w.lower() for w in re.findall(r'[0-9A-Za-z가-힣]+', text) if len(w) >= 2 and w not in _GENERIC_WORDS}


def related_posts(
    history: tuple[HistoryEntry, ...], labels: tuple[str, ...], limit: int = 3, topic: str = ''
) -> tuple[HistoryEntry, ...]:
    """같은 주제 글(제목 낱말이 겹치는 글)을 먼저, 그다음 라벨을 많이 공유하는 글 순(같으면 최신순)으로 limit개.
    내부 링크로 색인·체류를 돕는다. 2026-10-15: 라벨만 보면 주제와 무관한 최근 글이 붙어 주제 낱말을 우선한다."""
    wanted, words = set(labels), topic_words(topic)
    scored = []
    for e in reversed(history):
        if not e.url:
            continue
        shared = len(topic_words(e.title) & words)
        score = shared * 2 + len(set(e.labels) & wanted)
        if score:
            scored.append((score, e))
    ranked = sorted(scored, key=lambda m: -m[0])  # 안정 정렬: 동점은 최신순 유지
    return tuple(e for _, e in ranked[:limit])
