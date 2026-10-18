"""대한민국 휴무일(공휴일·대체공휴일·주말) 달력.

글이 "내일 월요일 출근"처럼 실제 달력과 어긋나지 않도록 날짜 맥락과 검수에 쓴다.
외부 패키지 없이 CI에서도 돌도록 표로 가지고 있다 (2026~2027, python-holidays 기준으로 대조).
표에 없는 해는 주말만 쉬는 날로 본다. 해가 바뀌기 전에 다음 해 표를 채울 것.
제헌절은 2026년 4월 관공서 공휴일로 지정돼 2026-10-18 넣었다 (우주항공청 2027년 월력요항: 2027-07-19 대체공휴일).
"""
from __future__ import annotations

import re
from datetime import date, timedelta

WEEKDAYS = '월화수목금토일'
HOLIDAYS: dict[date, str] = {
    date(2026, 1, 1): '신정', date(2026, 2, 16): '설날 연휴', date(2026, 2, 17): '설날', date(2026, 2, 18): '설날 연휴',
    date(2026, 3, 1): '삼일절', date(2026, 3, 2): '삼일절 대체공휴일', date(2026, 5, 1): '노동절',
    date(2026, 5, 5): '어린이날', date(2026, 5, 24): '부처님오신날', date(2026, 5, 25): '부처님오신날 대체공휴일',
    date(2026, 6, 3): '지방선거일', date(2026, 6, 6): '현충일', date(2026, 7, 17): '제헌절', date(2026, 8, 15): '광복절',
    date(2026, 8, 17): '광복절 대체공휴일', date(2026, 9, 24): '추석 연휴', date(2026, 9, 25): '추석',
    date(2026, 9, 26): '추석 연휴', date(2026, 10, 3): '개천절', date(2026, 10, 5): '개천절 대체공휴일',
    date(2026, 10, 9): '한글날', date(2026, 12, 25): '크리스마스',
    date(2027, 1, 1): '신정', date(2027, 2, 6): '설날 연휴', date(2027, 2, 7): '설날', date(2027, 2, 8): '설날 연휴',
    date(2027, 2, 9): '설날 대체공휴일', date(2027, 3, 1): '삼일절', date(2027, 5, 1): '노동절',
    date(2027, 5, 3): '노동절 대체공휴일', date(2027, 5, 5): '어린이날', date(2027, 5, 13): '부처님오신날',
    date(2027, 6, 6): '현충일', date(2027, 7, 17): '제헌절', date(2027, 7, 19): '제헌절 대체공휴일',
    date(2027, 8, 15): '광복절', date(2027, 8, 16): '광복절 대체공휴일',
    date(2027, 9, 14): '추석 연휴', date(2027, 9, 15): '추석', date(2027, 9, 16): '추석 연휴',
    date(2027, 10, 3): '개천절', date(2027, 10, 4): '개천절 대체공휴일', date(2027, 10, 9): '한글날',
    date(2027, 10, 11): '한글날 대체공휴일', date(2027, 12, 25): '크리스마스',
    date(2027, 12, 27): '크리스마스 대체공휴일',
}
# "내일(월요일) 출근"처럼 내일 일하러 간다는 말
_COMMUTE_TOMORROW = re.compile(r'(내일|월요일|낼)[^\n.!?]{0,12}(출근|회사 가|출근길)|(출근|회사 가)[^\n.!?]{0,8}(내일|월요일)')

_NEGATION = re.compile(r'안 ?(가|해|하|나)|없|쉬는|쉰|휴무|휴일|공휴일')


def day_off_name(day: date) -> str | None:
    """쉬는 날이면 이름(공휴일명·토요일·일요일), 평일이면 None."""
    name = HOLIDAYS.get(day)
    if name:
        return name
    if day.weekday() >= 5:
        return f'{WEEKDAYS[day.weekday()]}요일'
    return None


def is_day_off(day: date) -> bool:
    return day_off_name(day) is not None


def _label(day: date) -> str:
    return f'{day.month}월 {day.day}일 {WEEKDAYS[day.weekday()]}요일'


def describe(day: date) -> str:
    """프롬프트에 넣을 달력 사실. 오늘·내일이 쉬는 날인지 분명하게 적는다."""
    today, tomorrow = HOLIDAYS.get(day), day + timedelta(days=1)
    lines = []
    if today:
        lines.append(f'오늘은 {today}라서 쉬는 날 (평일이어도 회사에 안 가)')
    elif day.weekday() >= 5:
        lines.append(f'오늘은 {WEEKDAYS[day.weekday()]}요일이라 쉬는 날')
    next_name = day_off_name(tomorrow)
    if next_name and HOLIDAYS.get(tomorrow):
        lines.append(f'내일({_label(tomorrow)})은 {next_name}라서 쉬는 날: 내일 출근하지 않아. '
                     '"내일 출근", "월요일 출근 준비", 일요일 밤 출근 걱정 소재는 쓰지 마')
    elif next_name:
        lines.append(f'내일은 {next_name}이라 쉬는 날')
    else:
        lines.append(f'내일은 출근하는 평일({_label(tomorrow)})')
    return '\n'.join(lines)


def commute_issues(text: str, day: date) -> list[str]:
    """내일이 평일 공휴일(대체공휴일 포함)인데 내일 출근한다고 쓴 글을 잡는다. "출근 안 해"는 통과."""
    tomorrow = day + timedelta(days=1)
    if not (tomorrow in HOLIDAYS and tomorrow.weekday() < 5):
        return []
    for m in _COMMUTE_TOMORROW.finditer(text):
        if not _NEGATION.search(text[m.start():m.end() + 8]):
            return [f'내일({_label(tomorrow)})은 {HOLIDAYS[tomorrow]}라서 쉬는 날이야. 내일 출근한다는 내용은 빼고 다시 써.']
    return []


# --- 블로그용 확장: 연휴 맥락, 시의성 표현 검수 ---

_UPCOMING_DAYS = 7
_SENTENCE = re.compile(r'[^.!?。\n]+[.!?。]*')
_WEEKDAY_COMMUTE = re.compile(r'([월화수목금])요일[^\n.!?]{0,12}(출근|회사 가|출근길)')
_TODAY_COMMUTE = re.compile(r'오늘[^\n.!?]{0,8}(출근|퇴근)')
_LAST_DAY = re.compile(r'연휴\s*(마지막\s*날|마지막날|끝자락)')
_SKIP_NEXT_WEEK = re.compile(r'다음\s*주|다다음|지난')


def _run_of(day: date) -> list[date]:
    """day가 쉬는 날이면 그날이 낀 연속 휴무일 묶음(주말 포함), 아니면 빈 목록."""
    if not is_day_off(day):
        return []
    start = day
    while is_day_off(start - timedelta(days=1)):
        start -= timedelta(days=1)
    run = [start]
    while is_day_off(run[-1] + timedelta(days=1)):
        run.append(run[-1] + timedelta(days=1))
    return run


def _short(day: date) -> str:
    return f'{day.month}/{day.day}({WEEKDAYS[day.weekday()]})'


def holiday_context(day: date) -> str:
    """연휴 중이거나 휴일이 코앞이면 글에 살릴 맥락. 평범한 주중이면 빈 문자열."""
    lines = []
    run = _run_of(day)
    if len(run) >= 3 and any(d in HOLIDAYS for d in run):
        names = ', '.join(dict.fromkeys(HOLIDAYS[d] for d in run if d in HOLIDAYS))
        lines.append(f'지금은 {_short(run[0])}~{_short(run[-1])} {len(run)}일 연휴({names}) 중이다.'
                     + (f' 오늘은 연휴 마지막 날이다.' if day == run[-1] else ''))
    else:
        for offset in range(1, _UPCOMING_DAYS + 1):
            ahead = _run_of(day + timedelta(days=offset))
            if ahead and any(d in HOLIDAYS for d in ahead) and not is_day_off(day):
                names = ', '.join(dict.fromkeys(HOLIDAYS[d] for d in ahead if d in HOLIDAYS))
                lines.append(f'{offset}일 뒤인 {_short(ahead[0])}부터 {_short(ahead[-1])}까지 {len(ahead)}일 쉬는 날({names})이다.')
                break
    if lines:
        lines.append('독자가 휴일·연휴를 앞두거나 보내는 중이니, 원문과 맞으면 휴일 준비, 휴일 영업·운영, 이동·소비·여가 맥락을 살려 써. '
                     '다만 휴일 운영 시간·영업 여부·혜택처럼 원문에 없는 사실은 지어내지 말고 "미리 확인해 두세요"처럼 안내만 해.')
    return '\n'.join(lines)


def calendar_facts(day: date) -> str:
    """프롬프트 맨 위에 넣는 달력 사실 전체: 요일·쉬는 날 여부·연휴 맥락·시의성 표현 규칙."""
    parts = [f'발행일: {day.year}년 {_label(day)}', describe(day)]
    context = holiday_context(day)
    if context:
        parts.append(context)
    parts.append('"내일", "이번 주말", "월요일 출근 전", "연휴 마지막 날" 같은 시점 표현은 위 달력과 맞을 때만 쓰고, 애매하면 쓰지 마.')
    return '\n'.join(parts)


def _problem(sentence: str, day: date) -> str | None:
    tomorrow = day + timedelta(days=1)
    clean = not _NEGATION.search(sentence) and not _SKIP_NEXT_WEEK.search(sentence)
    if clean and is_day_off(tomorrow) and _COMMUTE_TOMORROW.search(sentence):
        return f'내일({_label(tomorrow)})은 {day_off_name(tomorrow)}이라 쉬는 날인데 내일 출근한다고 썼다.'
    if clean and is_day_off(day) and _TODAY_COMMUTE.search(sentence):
        return f'오늘({_label(day)})은 {day_off_name(day)}이라 쉬는 날인데 오늘 출근·퇴근한다고 썼다.'
    if clean:
        for m in _WEEKDAY_COMMUTE.finditer(sentence):
            nxt = next(d for d in (day + timedelta(days=i) for i in range(1, 8)) if d.weekday() == WEEKDAYS.index(m.group(1)))
            if is_day_off(nxt):
                return f'다가오는 {_label(nxt)}은 {day_off_name(nxt)}이라 쉬는 날인데 {m.group(1)}요일에 출근한다고 썼다.'
    if _LAST_DAY.search(sentence) and not (is_day_off(day) and not is_day_off(tomorrow)):
        return f'오늘({_label(day)})은 연휴 마지막 날이 아니다.'
    return None


def timing_problems(text: str, day: date) -> list[tuple[str, str]]:
    """발행일 day 기준으로 실제 달력과 안 맞는 시점 표현이 든 문장. [(문장, 이유)]"""
    found = []
    for m in _SENTENCE.finditer(text):
        sentence = m.group(0).strip()
        reason = _problem(sentence, day) if sentence else None
        if reason:
            found.append((sentence, reason))
    return found
