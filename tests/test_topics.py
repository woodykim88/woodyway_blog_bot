"""분야 나누기: AI·IT 50% + 생활경제·재테크 50% (사용자 결정 2026-09-27)."""
from datetime import datetime, timezone
from pathlib import Path

from src import collector, renderer, topics, writer
from src.models import PostDraft
from tests.helpers import make_article, make_draft, make_entry

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / 'fixtures'
NOW = datetime(2026, 9, 26, 17, 30, tzinfo=timezone.utc)  # 2026-09-27 02:30 KST


def entries(*kinds):
    labels = {topics.TECH: ('AI활용',), topics.MONEY: ('재테크',)}
    return tuple(make_entry(title=f'글 {i}', labels=labels[k]) for i, k in enumerate(kinds))


def test_category_of_uses_labels():
    assert topics.category_of(make_entry(labels=('재테크', '업무생산성'))) == topics.MONEY
    assert topics.category_of(make_entry(labels=('AI활용',))) == topics.TECH


def test_choose_category_keeps_recent_posts_half_and_half():
    assert topics.choose_category(()) == topics.MONEY  # 처음엔 생활경제부터
    assert topics.choose_category(entries(topics.TECH, topics.TECH, topics.MONEY)) == topics.MONEY
    assert topics.choose_category(entries(topics.MONEY, topics.MONEY, topics.TECH)) == topics.TECH
    # 개수가 같으면 직전 글과 다른 분야
    assert topics.choose_category(entries(topics.MONEY, topics.TECH)) == topics.MONEY
    assert topics.choose_category(entries(topics.TECH, topics.MONEY)) == topics.TECH


def test_choose_category_looks_only_at_recent_window():
    old = entries(*([topics.MONEY] * 10))
    recent = entries(*([topics.TECH, topics.MONEY] * (topics.BALANCE_WINDOW // 2)))
    assert topics.choose_category(old + recent) == topics.TECH  # 최근 창은 반반, 직전 글이 생활경제


def test_every_category_has_sources_themes_and_prompts():
    for category in topics.CATEGORIES:
        spec = topics.SPECS[category]
        assert spec.sources and len(spec.themes) == 7 and spec.labels
        for name in (spec.selector_prompt, spec.persona_prompt):
            assert (ROOT / 'prompts' / f'{name}.md').exists()
    names = {s.name for s in topics.SPECS[topics.MONEY].sources}
    assert {'한국경제', '이투데이', '파이낸셜뉴스'} <= names
    assert not names & {'서울경제', '연합뉴스', '조선비즈', '머니투데이'}  # AI 수집을 막은 매체는 쓰지 않는다


def test_money_labels_are_valid_post_labels():
    allowed = set(PostDraft.model_fields['labels'].annotation.__args__[0].__args__)
    for category in topics.CATEGORIES:
        assert set(topics.SPECS[category].labels) <= allowed


def test_money_persona_forbids_investment_advice():
    persona = (ROOT / 'prompts' / f'{topics.SPECS[topics.MONEY].persona_prompt}.md').read_text(encoding='utf-8')
    assert '종목' in persona and '투자 권유' in persona


def test_writer_instructions_start_with_category_persona():
    money = writer.instructions(topics.MONEY)
    tech = writer.instructions(topics.TECH)
    assert money != tech and '생활경제' in money.split('\n')[0] + money.split('\n')[1]
    assert '[반드시 지킬 것]' in money and '[반드시 지킬 것]' in tech


def test_policy_list_parsing():
    items = collector.parse_policy_list((FIXTURES / 'policy_list.html').read_text(encoding='utf-8'), NOW)
    assert items and all(c.source == '정책브리핑' and c.direct for c in items)
    assert all(c.link.startswith('https://www.korea.kr/news/policyNewsView.do?newsId=') for c in items)
    assert not any('정상' in c.title for c in items)  # 외교·정치 일정은 뺀다


def test_money_post_gets_general_info_note():
    html = renderer.render_post(make_draft(labels=['재테크']), make_article())
    assert renderer.MONEY_NOTE in html
    assert renderer.MONEY_NOTE not in renderer.render_post(make_draft(labels=['AI활용']), make_article())
