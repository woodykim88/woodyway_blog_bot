"""스킬 글 소재: skills.sh 인기·급상승 스킬 중 하나를 골라 글의 원문(Article)으로 만든다.

루이 채널(@louis.ai.code)과 같은 출처·같은 안전 기준을 쓴다 (원 소스 멀티 유즈, 2026-09-30 사용자 결정).
- 설치 명령은 링크와 정확히 같을 때만 (skills.safe_install_command)
- 보안 감사 결과가 있고 Fail·모르는 상태가 없어야 한다 (Warn은 허용)
- 공식 조직이거나 GitHub 별이 충분해야 신뢰한다
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from src import skills
from src.history import HistoryEntry
from src.llm import LLMClient, load_prompt
from src.models import Article, Candidate, SkillFitResult

MIN_INSTALLS = 300
TRUST_MIN_STARS = 100
SHORTLIST = 12   # 상세 페이지를 읽어 볼 후보 수
MAX_PASSING = 8  # 대중성 점수를 매길 통과 후보 수 (모델 호출 1번에 담는다)
NAME_POOL = 80   # 이름만 보고 대중성을 거를 리더보드 후보 수 (문서·리서치 스킬은 순위가 낮게 흩어져 있다)
MIN_NAME_FIT = 5  # 이름 단계에서 이 점수 미만이면 상세 페이지를 읽지 않는다
MIN_FIT = 6      # 이 점수 미만이면 개발자 전용으로 보고 뉴스 글로 대신한다
SOURCE_NAME = 'skills.sh'


def reject_reason(skill: skills.Skill) -> str:
    """소개하면 안 되는 이유. 빈 문자열이면 통과."""
    if not skill.install_cmd:
        return '설치 명령을 읽지 못함'
    if not skill.audits_ok:
        return '보안 감사 결과 없음 또는 미통과'
    if not skill.bullets and not skill.skill_md:
        return '설명 자료가 없음'
    if skill.installs < MIN_INSTALLS:
        return f'설치 수 부족({skill.installs})'
    if not (skill.verified_org or skill.stars >= TRUST_MIN_STARS):
        return '신뢰 근거 부족(공식 조직 아님, 별 부족)'
    return ''


def _ordered(refs: Sequence[skills.SkillRef]) -> list[skills.SkillRef]:
    """두 보드에 다 오른 스킬 → Trending 순위순 → Hot 순위순 (루이 채널 selector.shortlist와 같은 순서)."""
    return sorted(refs, key=lambda r: (-len(r.boards), r.board != 'trending', r.rank))


def pick_skill(
    list_candidates: Callable[[], Sequence[skills.SkillRef]],
    fetch_detail: Callable[[skills.SkillRef], skills.Skill],
    past: Sequence[HistoryEntry],
    limit: int = SHORTLIST,
    choose: Callable[[Sequence[skills.Skill]], skills.Skill | None] | None = None,
    prefilter: Callable[[Sequence[skills.SkillRef]], Sequence[skills.SkillRef]] | None = None,
) -> skills.Skill | None:
    """아직 쓰지 않은 스킬 중 기준을 통과한 스킬. choose가 있으면 통과한 후보들 중에서 고르게 하고, 없으면 첫 통과작.

    없으면 None (그때는 뉴스 글로 대신한다).
    """
    used = {e.source_url for e in past}
    fresh = [r for r in _ordered(list_candidates()) if f'{skills.BASE}/{r.key}' not in used]
    if prefilter is not None:
        fresh = list(prefilter(fresh))  # 상세 페이지를 읽기 전에 유력한 순으로 줄인다
    passing: list[skills.Skill] = []
    for ref in fresh[:limit]:
        try:
            skill = fetch_detail(ref)
        except Exception as e:  # noqa: BLE001 - 상세 페이지 하나가 죽어도 다음 후보로
            print(f'[스킬 건너뜀] {ref.key}: {e.__class__.__name__}')
            continue
        reason = reject_reason(skill)
        if reason:
            print(f'[스킬 제외] {ref.key}: {reason}')
            continue
        if choose is None:
            return skill
        passing.append(skill)
        if len(passing) >= MAX_PASSING:
            break
    if not passing or choose is None:
        return None
    return choose(passing)


def rank_by_name(llm: LLMClient, model: str, refs: Sequence[skills.SkillRef], min_fit: int = MIN_NAME_FIT) -> list[skills.SkillRef]:
    """이름만 보고 대중적인 순으로 다시 세운다 (점수 높은 순, 같으면 원래 순서). min_fit 미만은 뺀다."""
    pool = list(refs[:NAME_POOL])
    if not pool:
        return []
    lines = '\n'.join(f'[{i}] {r.name} ({r.owner_repo})' for i, r in enumerate(pool))
    result = llm.parse(model, load_prompt('skill_name_fit'), lines, SkillFitResult, stage='skill_pick')
    best: dict[int, int] = {}
    for s in result.scores:
        if 0 <= s.index < len(pool) and s.index not in best:
            best[s.index] = s.score
    kept = sorted((i for i, score in best.items() if score >= min_fit), key=lambda i: (-best[i], i))
    return [pool[i] for i in kept]


def format_skills(candidates: Sequence[skills.Skill]) -> str:
    lines = []
    for i, s in enumerate(candidates):
        points = ' / '.join(s.bullets[:3])
        lines.append(f'[{i}] {s.name} ({s.ref.owner_repo}) 분류: {s.category or "-"}\n    소개: {s.headline}\n    기능: {points}')
    return '\n'.join(lines)


def choose_popular(llm: LLMClient, model: str, candidates: Sequence[skills.Skill], min_fit: int = MIN_FIT) -> skills.Skill | None:
    """비개발 직장인에게 대중적인 스킬을 고른다 (점수 최고, 같으면 앞선 것). min_fit 미만뿐이면 None."""
    if not candidates:
        return None
    result = llm.parse(model, load_prompt('skill_fit'), format_skills(candidates), SkillFitResult, stage='skill_pick')
    valid = [s for s in result.scores if 0 <= s.index < len(candidates)]
    if not valid:
        return None
    for s in valid:
        print(f'[스킬 대중성] {s.score}점 {candidates[s.index].key}')
    best = max(valid, key=lambda s: (s.score, -s.index))
    return candidates[best.index] if best.score >= min_fit else None


def to_article(skill: skills.Skill, now: datetime) -> Article:
    """스킬 페이지에서 읽은 사실을 글쓰기·사실검증용 원문으로 묶는다. 숫자는 페이지 값 그대로."""
    facts = [
        f'스킬 이름: {skill.name}',
        f'저장소: {skill.ref.owner_repo} ({skill.github_url})',
        f'분류: {skill.category or "-"}',
        f'한 줄 소개: {skill.headline}',
        *(f'- {b}' for b in skill.bullets),
        f'누적 설치 수: {skill.installs:,}회',
        f'GitHub 별: {skill.stars:,}개',
        f'처음 등록: {skill.first_seen or "-"}',
        f'설치 명령(글에는 코드가 따로 넣는다): {skill.install_cmd}',
        '--- SKILL.md 원문 ---',
        skill.skill_md,
    ]
    candidate = Candidate(
        title=f'{skill.name} ({skill.ref.owner_repo})', link=skill.url, summary=skill.headline,
        published=now, source=SOURCE_NAME, direct=True,
    )
    return Article(candidate=candidate, source_url=skill.url, text='\n'.join(facts))
