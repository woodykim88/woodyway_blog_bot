"""발행 이력(data/history.json). 중복 주제 방지와 중복 발행 방지(멱등성)에 쓴다."""
from __future__ import annotations

import json
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
    geeknews_url: str
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


def related_posts(
    history: tuple[HistoryEntry, ...], labels: tuple[str, ...], limit: int = 2
) -> tuple[HistoryEntry, ...]:
    """같은 라벨을 가진 최근 글을 최신순으로 limit개 반환합니다."""
    matches = [e for e in reversed(history) if e.url and set(e.labels) & set(labels)]
    return tuple(matches[:limit])
