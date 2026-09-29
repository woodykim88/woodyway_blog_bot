from datetime import datetime
from io import BytesIO

from PIL import Image

from src import main as pipeline
from src import schedule, skill_topic, skills, thumbnail, topics
from src.renderer import render_post
from src.models import SkillFitResult, SkillFitScore
from tests.helpers import fake_llm, make_article, make_draft, make_entry

NOW = datetime(2026, 9, 30, 10, 17, tzinfo=schedule.KST)
PASS3 = {'Gen Agent Trust Hub': 'Pass', 'Socket': 'Pass', 'Snyk': 'Pass'}


def make_skill(name='frontend-design', owner_repo='anthropics/skills', **overrides) -> skills.Skill:
    ref = skills.SkillRef(owner_repo, name, rank=1, metric=5000, board='trending', boards=('trending', 'hot'))
    fields = {
        'ref': ref, 'boards': ref.boards, 'category': 'Design & UI',
        'headline': 'Production-grade frontend interfaces.',
        'bullets': ('Picks a bold visual direction', 'Writes working code'),
        'install_cmd': f'npx skills add https://github.com/{owner_repo} --skill {name}',
        'installs': 93_000, 'stars': 1_200, 'first_seen': 'Jan 2026',
        'audits': dict(PASS3), 'verified_org': True, 'skill_md': 'Use this skill when building UI. ' * 20,
    }
    return skills.Skill(**{**fields, **overrides})


# --- 스킬 고르기 ---

def test_reject_reason_blocks_unsafe_or_unproven_skills():
    assert skill_topic.reject_reason(make_skill()) == ''
    assert '설치 명령' in skill_topic.reject_reason(make_skill(install_cmd=''))
    assert '보안 감사' in skill_topic.reject_reason(make_skill(audits={}))
    assert '보안 감사' in skill_topic.reject_reason(make_skill(audits={**PASS3, 'Snyk': 'Fail'}))
    assert '설치 수' in skill_topic.reject_reason(make_skill(installs=120))
    assert '신뢰' in skill_topic.reject_reason(make_skill(verified_org=False, stars=40))
    assert skill_topic.reject_reason(make_skill(audits={**PASS3, 'Snyk': 'Warn'})) == ''  # 루이 채널과 같은 기준


def test_pick_skill_skips_used_and_rejected_candidates():
    good, used, bad = make_skill('good-one'), make_skill('already-used'), make_skill('no-audit', audits={})
    refs = [bad.ref, used.ref, good.ref]
    details = {s.ref.key: s for s in (good, used, bad)}
    past = (make_entry(source_url=used.url),)

    picked = skill_topic.pick_skill(lambda: refs, lambda ref: details[ref.key], past)
    assert picked == good


def test_pick_skill_survives_a_broken_detail_page_and_returns_none_when_nothing_fits():
    good = make_skill('good-one')

    def detail(ref):
        if ref.name == 'broken':
            raise OSError('timeout')
        return good

    broken = make_skill('broken').ref
    assert skill_topic.pick_skill(lambda: [broken, good.ref], detail, ()) == good
    assert skill_topic.pick_skill(lambda: [broken], detail, ()) is None


def test_pick_skill_hands_all_passing_candidates_to_chooser():
    skills_by_key = {s.ref.key: s for s in (make_skill('a'), make_skill('b', audits={}), make_skill('c'), make_skill('d'))}
    seen = []

    def choose(found):
        seen.append([s.name for s in found])
        return found[-1]

    picked = skill_topic.pick_skill(lambda: [s.ref for s in skills_by_key.values()], lambda ref: skills_by_key[ref.key], (), choose=choose)
    assert seen == [['a', 'c', 'd']]  # 검수 탈락(b)은 넘기지 않는다
    assert picked.name == 'd'


def test_pick_skill_returns_none_when_chooser_rejects_everything():
    s = make_skill('dev-only')
    assert skill_topic.pick_skill(lambda: [s.ref], lambda ref: s, (), choose=lambda found: None) is None


def fit(*scores):
    return SkillFitResult(scores=[SkillFitScore(index=i, score=sc, reason='r') for i, sc in scores])


def test_choose_popular_picks_highest_scoring_skill_for_non_developers():
    candidates = [make_skill('solana-dev', 'solana-foundation/solana-dev-skill'), make_skill('pdf'), make_skill('docx')]
    llm, client = fake_llm({'SkillFitResult': [fit((0, 2), (1, 9), (2, 7))]})
    assert skill_topic.choose_popular(llm, 'fast', candidates).name == 'pdf'
    assert 'solana-dev' in client.requests[0]['input'] and 'Production-grade' in client.requests[0]['input']


def test_choose_popular_returns_none_when_nothing_is_popular_enough():
    llm, _ = fake_llm({'SkillFitResult': [fit((0, 3), (1, 5))]})
    assert skill_topic.choose_popular(llm, 'fast', [make_skill('a'), make_skill('b')]) is None


def test_choose_popular_ignores_out_of_range_index_and_breaks_ties_by_order():
    llm, _ = fake_llm({'SkillFitResult': [fit((7, 10), (1, 8), (0, 8))]})
    assert skill_topic.choose_popular(llm, 'fast', [make_skill('a'), make_skill('b')]).name == 'a'


def refs_named(*names):
    return [skills.SkillRef('some/repo', n, rank=i + 1, metric=1, board='trending') for i, n in enumerate(names)]


def test_rank_by_name_puts_popular_names_first_and_drops_developer_only_ones():
    refs = refs_named('solana-dev', 'research', 'slideshow', 'prisma-client-api')
    llm, client = fake_llm({'SkillFitResult': [fit((0, 2), (1, 9), (2, 7), (3, 1))]})
    ranked = skill_topic.rank_by_name(llm, 'fast', refs)
    assert [r.name for r in ranked] == ['research', 'slideshow']
    assert 'solana-dev (some/repo)' in client.requests[0]['input']


def test_rank_by_name_keeps_original_order_for_ties_and_only_looks_at_pool():
    refs = refs_named(*[f's{i}' for i in range(skill_topic.NAME_POOL + 5)])
    scores = [(i, 8) for i in range(len(refs))]
    llm, client = fake_llm({'SkillFitResult': [fit(*scores)]})
    ranked = skill_topic.rank_by_name(llm, 'fast', refs)
    assert [r.name for r in ranked][:3] == ['s0', 's1', 's2']
    assert f'[{skill_topic.NAME_POOL}]' not in client.requests[0]['input']


def test_pick_skill_applies_prefilter_before_reading_detail_pages():
    a, b = make_skill('a'), make_skill('b')
    fetched = []

    def detail(ref):
        fetched.append(ref.name)
        return {'a': a, 'b': b}[ref.name]

    picked = skill_topic.pick_skill(lambda: [a.ref, b.ref], detail, (), prefilter=lambda refs: [refs[-1]])
    assert fetched == ['b'] and picked == b


def test_skill_article_carries_facts_and_source_url():
    skill = make_skill()
    article = skill_topic.to_article(skill, NOW)
    assert article.source_url == skill.url
    assert article.candidate.source == 'skills.sh' and article.candidate.direct
    assert 'frontend-design' in article.text and '93,000' in article.text and 'Use this skill' in article.text


# --- 분야 비율: 스킬 글은 뉴스 반반 계산에서 뺀다 ---

def test_skill_posts_do_not_count_toward_news_balance():
    past = (make_entry(labels=('재테크',)), make_entry(labels=('AI스킬', 'AI활용')), make_entry(labels=('AI스킬',)))
    assert topics.category_of(past[1]) == topics.SKILL
    # 뉴스만 보면 생활경제 1 : AI·IT 0 → AI·IT 차례
    assert topics.choose_category(past) == topics.TECH


def test_is_skill_slot():
    assert pipeline.is_skill_slot(datetime(2026, 9, 30, 12, 30, tzinfo=schedule.KST), (12, 30))
    assert not pipeline.is_skill_slot(datetime(2026, 9, 30, 20, 0, tzinfo=schedule.KST), (12, 30))
    assert not pipeline.is_skill_slot(None, (12, 30))


def test_skill_draft_always_gets_skill_label_first():
    draft = pipeline.force_skill_labels(make_draft(labels=['툴추천']))
    assert draft.labels[0] == 'AI스킬' and '툴추천' in draft.labels
    assert pipeline.force_skill_labels(make_draft(labels=['AI스킬'])).labels == ['AI스킬']


# --- 글 본문: 설치 정보는 LLM이 아니라 코드가 페이지 값으로 넣는다 ---

def test_skill_box_shows_page_facts_and_pass_chip_only_when_all_pass():
    skill = make_skill()
    html = render_post(make_draft(), make_article(), skill=skill)
    assert skill.install_cmd in html and '93,000' in html and skill.github_url in html
    assert '보안 감사 3종 모두 통과' in html
    warned = render_post(make_draft(), make_article(), skill=make_skill(audits={**PASS3, 'Snyk': 'Warn'}))
    assert '보안 감사 3종 모두 통과' not in warned
    assert 'npx skills add' not in render_post(make_draft(), make_article())


# --- 대표 이미지: 루이는 AI·IT·스킬 글에만 ---

def _png(size=(1200, 800), color=(30, 30, 30)) -> bytes:
    buf = BytesIO()
    Image.new('RGB', size, color).save(buf, 'PNG')
    return buf.getvalue()


def test_mascot_for_category():
    assert thumbnail.mascot_for(topics.SKILL).name == 'louis-laptop.png'
    assert thumbnail.mascot_for(topics.TECH).name == 'louis-front.png'
    assert thumbnail.mascot_for(topics.MONEY) is None


def test_add_mascot_paints_bottom_right_only():
    out = Image.open(BytesIO(thumbnail.add_mascot(_png(), thumbnail.mascot_for(topics.SKILL)))).convert('RGB')
    assert out.size == (1200, 800)
    assert out.getpixel((1050, 700)) != (30, 30, 30)   # 오른쪽 아래에 루이
    assert out.getpixel((100, 400)) == (30, 30, 30)    # 제목이 있는 왼쪽은 그대로
