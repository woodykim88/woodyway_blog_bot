from datetime import datetime
from io import BytesIO

from PIL import Image

from src import main as pipeline
from src import schedule, skill_topic, skills, thumbnail, topics
from src.renderer import render_post
from tests.helpers import make_article, make_draft, make_entry

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
