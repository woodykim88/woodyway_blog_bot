import json
from datetime import date

from src import topic_hints


def write(tmp_path, day='2026-10-15'):
    path = tmp_path / 'hints.json'
    path.write_text(json.dumps({'date': day, 'channels': {'blog': [
        {'topic': '독감 접종', 'keywords': ['독감', '예방 접종'], 'angle': '시기'}]}}, ensure_ascii=False), encoding='utf-8')
    return path


def test_load_recent_hints_for_channel(tmp_path):
    hints = topic_hints.load(date(2026, 10, 16), write(tmp_path))
    assert [h['topic'] for h in hints] == ['독감 접종']


def test_load_ignores_old_missing_or_other_channel(tmp_path):
    assert topic_hints.load(date(2026, 10, 20), write(tmp_path)) == []
    assert topic_hints.load(date(2026, 10, 15), tmp_path / 'none.json') == []
    assert topic_hints.load(date(2026, 10, 15), write(tmp_path), channel='other') == []


def test_matches_ignores_spacing():
    hints = [{'topic': '독감 접종', 'keywords': ['예방 접종']}]
    assert topic_hints.matches('예방접종 시기 앞당겨', hints) == ['독감 접종']
    assert topic_hints.matches('감기약 품절', hints) == []


def test_brief_lists_topics_or_is_empty():
    assert topic_hints.brief([], 'x') == ''
    text = topic_hints.brief([{'topic': '독감 접종', 'keywords': ['독감'], 'angle': '시기'}], '쓰임')
    assert '독감 접종' in text and '쓰임' in text
