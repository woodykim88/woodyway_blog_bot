"""발행 시각(슬롯) 계산. 외부 라이브러리 없이 동작해야 한다 (GitHub Actions 사전 점검 단계에서 사용).

- PUBLISH_TIMES: "20:00" 또는 "08:00,12:30,20:00" (KST, 하루 최대 3회)
- 슬롯 키: "2026-09-27T20:00" 형식. 이 키로 이미 준비한 발행분인지 판단한다.
- 자동 실행은 앞으로 LEAD_HOURS 안에 돌아오는 슬롯만 준비한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), 'KST')
MAX_POSTS_PER_DAY = 3
DEFAULT_TIMES = '20:00'
LEAD_HOURS = 6          # 발행 6시간 전부터 준비
MIN_LEAD_MINUTES = 10   # 발행 10분 전보다 늦으면 준비하지 않음 (생성 시간 확보)

Time = tuple[int, int]


class ScheduleError(ValueError):
    """발행 시각 설정이 잘못되었을 때 발생합니다."""


def parse_times(value: str | None) -> tuple[Time, ...]:
    """'08:00, 20:00' → ((8, 0), (20, 0)). 중복 제거 후 시간순 정렬."""
    raw = [part.strip() for part in (value or DEFAULT_TIMES).split(',') if part.strip()]
    times = set()
    for part in raw:
        pieces = part.split(':')
        if len(pieces) != 2 or not all(p.isdigit() for p in pieces):
            raise ScheduleError(f'발행 시각은 HH:MM 형식이어야 합니다. (현재: {part!r})')
        hour, minute = int(pieces[0]), int(pieces[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ScheduleError(f'올바르지 않은 시각입니다. (현재: {part!r})')
        times.add((hour, minute))
    if not times:
        raise ScheduleError('발행 시각이 하나 이상 필요합니다.')
    if len(times) > MAX_POSTS_PER_DAY:
        raise ScheduleError(f'하루 발행은 최대 {MAX_POSTS_PER_DAY}회입니다. (현재: {len(times)}회)')
    return tuple(sorted(times))


def format_times(times: tuple[Time, ...]) -> str:
    return ','.join(f'{h:02d}:{m:02d}' for h, m in times)


def slot_key(moment: datetime) -> str:
    return moment.astimezone(KST).strftime('%Y-%m-%dT%H:%M')


def slot_datetime(key: str) -> datetime:
    return datetime.strptime(key, '%Y-%m-%dT%H:%M').replace(tzinfo=KST)


def upcoming_slots(now: datetime, times: tuple[Time, ...], days: int = 2) -> list[datetime]:
    """now 이후의 슬롯을 시간순으로 반환합니다."""
    if now.tzinfo is None:
        raise ValueError('now에는 시간대 정보가 있어야 합니다.')
    today = now.astimezone(KST).date()
    slots = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        for hour, minute in times:
            slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)
            if slot > now:
                slots.append(slot)
    return sorted(slots)


def due_slots(
    now: datetime,
    times: tuple[Time, ...],
    done: set[str],
    lead_hours: int = LEAD_HOURS,
    min_lead_minutes: int = MIN_LEAD_MINUTES,
) -> list[datetime]:
    """지금 준비해야 할(아직 안 만든, lead_hours 안에 돌아오는) 슬롯."""
    earliest = now + timedelta(minutes=min_lead_minutes)
    latest = now + timedelta(hours=lead_hours)
    return [s for s in upcoming_slots(now, times) if earliest <= s <= latest and slot_key(s) not in done]


def next_open_slot(now: datetime, times: tuple[Time, ...], done: set[str]) -> datetime | None:
    """아직 준비하지 않은 가장 가까운 슬롯 (수동 '다음 시각 예약'용)."""
    earliest = now + timedelta(minutes=MIN_LEAD_MINUTES)
    for slot in upcoming_slots(now, times, days=3):
        if slot >= earliest and slot_key(slot) not in done:
            return slot
    return None
