"""한국 휴일 달력: 프롬프트 주입, 시점 표현 검수, 구제 발행 (2026-10-04, 개천절 대체공휴일 10/5 월)."""
from datetime import date

from src import kr_calendar as cal
from src import main as pipeline
from src import quality, rescue, selector, writer
from src.models import FactCheck, Section
from tests.helpers import fake_llm, make_article, make_candidate, make_draft

SUN = date(2026, 10, 4)   # 일요일, 내일 월요일은 개천절 대체공휴일
SAT = date(2026, 10, 3)   # 개천절(토)
MON = date(2026, 10, 5)   # 대체공휴일 (연휴 마지막 날)
TUE = date(2026, 10, 6)
OK = FactCheck(claims=[])
ALIVE = lambda url: True  # noqa: E731
MODELS = pipeline.Models(writer='w', fast='f')


def test_day_off_and_describe():
    assert cal.day_off_name(MON) == '개천절 대체공휴일'
    assert not cal.is_day_off(TUE)
    text = cal.describe(SUN)
    assert '내일(10월 5일 월요일)은 개천절 대체공휴일' in text and '출근하지 않아' in text
    assert '내일은 출근' in cal.describe(date(2026, 10, 11))


def test_unknown_year_is_weekend_only():
    assert not cal.is_day_off(date(2031, 1, 8)) and cal.is_day_off(date(2031, 1, 11))


def test_holiday_context_during_run_and_before():
    during = cal.holiday_context(SUN)
    assert '10/3(토)~10/5(월) 3일 연휴' in during and '지어내지' in during
    assert '연휴 마지막 날' in cal.holiday_context(MON)
    before = cal.holiday_context(date(2026, 10, 1))  # 목: 2일 뒤 토요일부터 3일 연휴
    assert '10/3(토)부터 10/5(월)까지 3일' in before
    assert cal.holiday_context(date(2026, 10, 13)) == ''  # 평범한 주


def test_calendar_facts_has_date_and_rule():
    facts = cal.calendar_facts(SUN)
    assert '2026년 10월 4일 일요일' in facts and '시점 표현' in facts


def test_timing_flags_tomorrow_commute_on_substitute_holiday():
    found = cal.timing_problems('내일 월요일 출근이 걱정이죠. 그래도 힘내요.', SUN)
    assert len(found) == 1 and '출근' in found[0][0] and '개천절 대체공휴일' in found[0][1]


def test_timing_allows_negation_and_normal_week():
    assert cal.timing_problems('내일은 출근 안 해서 여유롭다.', SUN) == []
    assert cal.timing_problems('내일 출근길에 써 보세요.', date(2026, 10, 11)) == []


def test_timing_weekday_commute_on_holiday_monday():
    assert cal.timing_problems('월요일 출근 전에 설정해 두세요.', SUN)
    assert cal.timing_problems('화요일 출근 전에 설정해 두세요.', SUN) == []
    assert cal.timing_problems('다음 주 월요일 출근 전에 확인하세요.', SUN) == []


def test_timing_today_commute_on_day_off():
    assert cal.timing_problems('오늘 출근길에 확인하세요.', SAT)
    assert cal.timing_problems('오늘 출근길에 확인하세요.', TUE) == []


def test_timing_last_day_of_holiday():
    assert cal.timing_problems('연휴 마지막 날이라 정리가 필요하다.', SUN)
    assert cal.timing_problems('연휴 마지막 날이라 정리가 필요하다.', MON) == []


# --- 프롬프트 주입 ---

def test_writer_input_has_calendar_only_when_day_given():
    article = make_article()
    assert '달력 사실' not in writer.build_writer_input(article, '테마')
    text = writer.build_writer_input(article, '테마', day=SUN)
    assert '개천절 대체공휴일' in text and text.index('달력 사실') < text.index('원문 출처')


def test_selector_input_has_calendar_and_holiday_hint():
    llm, client = fake_llm({'SelectionResult': [__import__('src.models', fromlist=['x']).SelectionResult(scores=[])]})
    selector.rank_candidates(llm, 'fast', [make_candidate(0)], '테마', day=SUN)
    sent = str(client.requests[0]['input'])
    assert '개천절 대체공휴일' in sent and '휴일 맥락' in sent


# --- 검수 ---

def wrong_draft():
    sections = [Section(heading=f'궁금증 {i}', body='설명입니다. ' * 20, tip='바로 해 보세요.') for i in range(3)]
    return make_draft(sections=sections, why_it_matters='내일 월요일 출근 전에 꼭 켜 두세요. 시간이 절약됩니다.')


def test_gate_flags_wrong_timing_as_rescuable_issue():
    llm, _ = fake_llm({'FactCheck': [OK]})
    report = quality.run_quality_gate(llm, 'f', wrong_draft(), make_article(), (), ALIVE, SUN)
    assert any('달력과 맞지 않는 표현' in i for i in report.issues)
    assert report.rescuable and any('월요일 출근' in s for s in report.unsupported)


def test_gate_ignores_calendar_without_day():
    llm, _ = fake_llm({'FactCheck': [OK]})
    report = quality.run_quality_gate(llm, 'f', wrong_draft(), make_article(), (), ALIVE)
    assert not any('달력' in i for i in report.issues)


def test_rescue_removes_wrong_timing_sentence_only():
    llm, _ = fake_llm({'FactCheck': [OK, OK]})
    draft = wrong_draft()
    report = quality.run_quality_gate(llm, 'f', draft, make_article(), (), ALIVE, SUN)
    fixed = rescue.rescue(llm, 'f', draft, make_article(), report.unsupported, (), ALIVE, SUN)
    assert fixed is not None
    assert '월요일 출근' not in fixed.why_it_matters and '시간이 절약됩니다' in fixed.why_it_matters


def test_factcheck_input_has_article_date_for_relative_years():
    """2026-10-21: '올해 1~9월'을 '2026년 1~9월'로 바꾼 문장이 근거 없음으로 빠져 핵심 문장이 사라졌다.
    검수에 원문 기사 날짜를 함께 넘겨 올해·지난해·내년을 숫자 연도로 바꾼 것을 근거로 인정하게 한다."""
    llm, client = fake_llm({'FactCheck': [OK]})
    quality.check_fact_claims(llm, 'fast', make_draft(), make_article())
    sent = str(client.requests[0]['input'])
    assert '원문 기사 날짜: 2026-09-25' in sent and sent.index('원문 기사 날짜') < sent.index('--- 원문 ---')


def test_factcheck_prompt_accepts_relative_year_conversion():
    prompt = quality.load_prompt('factcheck')
    assert '올해' in prompt and '원문 기사 날짜' in prompt
