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
from src.models import Article, Candidate

MIN_INSTALLS = 300
TRUST_MIN_STARS = 100
SHORTLIST = 12   # 상세 페이지를 읽어 볼 후보 수
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
) -> skills.Skill | None:
    """아직 쓰지 않은 스킬 중 기준을 통과한 첫 스킬. 없으면 None (그때는 뉴스 글로 대신한다)."""
    used = {e.source_url for e in past}
    fresh = [r for r in _ordered(list_candidates()) if f'{skills.BASE}/{r.key}' not in used]
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
        return skill
    return None


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
