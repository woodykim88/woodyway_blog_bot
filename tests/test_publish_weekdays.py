from datetime import datetime, timezone

import pytest

from src import main


def _target(iso: str) -> main.Target:
    return main.Target(iso, datetime.fromisoformat(iso), 'schedule')


def test_parse_weekdays_korean_and_numbers():
    assert main.parse_weekdays('월수금') == frozenset({0, 2, 4})
    assert main.parse_weekdays('0,2,4') == frozenset({0, 2, 4})
    assert main.parse_weekdays('') is None
    assert main.parse_weekdays(None) is None


def test_parse_weekdays_rejects_garbage():
    with pytest.raises(ValueError):
        main.parse_weekdays('abc')


def test_filter_weekdays_uses_kst_day():
    # 2026-11-20 20:00 KST 는 금요일, 2026-11-21 20:00 KST 는 토요일
    fri = _target('2026-11-20T11:00:00+00:00')
    sat = _target('2026-11-21T11:00:00+00:00')
    kept = main.filter_weekdays([fri, sat], frozenset({0, 2, 4}))
    assert kept == [fri]


def test_filter_weekdays_none_keeps_all():
    t = [_target('2026-11-21T11:00:00+00:00')]
    assert main.filter_weekdays(t, None) == t
