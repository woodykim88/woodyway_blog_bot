"""(루이 채널 newsmanwoody_cardnews_bot의 src/skills.py를 그대로 가져왔다. 고칠 때 양쪽을 함께 본다.)
소재 수집: skills.sh 리더보드(Trending 24h·Hot)에서 스킬 후보를 모으고, 상세 페이지에서 근거 자료를 읽는다.

- skills.sh는 robots.txt가 페이지 수집을 허용한다(/api/·/search만 금지, 2026-09-30 확인). 그래서 API가 아니라 공개 페이지만 읽는다.
- PulseMCP는 봇 차단(Cloudflare) 화면이 떠서, ClawHub는 피드만 허용해서 뺐다. 차단을 우회하지 않는다.
- 숫자(설치 수·별·순위)와 설치 명령은 LLM이 쓰지 않고 이 모듈이 페이지에서 그대로 읽어 코드가 카드에 넣는다.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

BASE = 'https://skills.sh'
BOARD_URLS = {'trending': f'{BASE}/trending', 'hot': f'{BASE}/hot'}
MAX_SKILL_MD_CHARS = 6000
GOOD_AUDIT = 'pass'
WARN_AUDIT = 'warn'  # 경고는 소개하되 영상·캡션에 그대로 알린다 (2026-09-30 사용자 결정). Fail·모르는 상태는 소개하지 않는다
_COUNT_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*([KkMm]?)\s*$')
_UNITS = {'': 1, 'k': 1_000, 'm': 1_000_000}
_ROW_RE = re.compile(
    r'<a\b[^>]*href="/(?P<path>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"[^>]*>(?P<body>.*?)</a>', re.S)
_TAG_RE = re.compile(r'<[^>]+>')
_INSTALL_RE = re.compile(r'^npx skills add https://github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+) --skill (?P<skill>[A-Za-z0-9_.-]+)$')
_STRIP_RE = re.compile(r'<script.*?</script>|<style.*?</style>|<svg.*?</svg>', re.S)

HttpGet = Callable[[str], str]


class SourceError(Exception):
    """소재 페이지를 하나도 읽지 못했을 때 발생합니다."""


@dataclass(frozen=True)
class SkillRef:
    """리더보드의 한 줄."""

    owner_repo: str  # anthropics/skills
    name: str        # frontend-design
    rank: int
    metric: int      # 그 보드의 숫자 (Trending은 24시간 설치 수 쪽)
    board: str       # 처음 읽은 보드: trending | hot
    boards: tuple[str, ...] = ()  # 이 스킬이 오른 모든 보드 (둘 다면 상승세가 더 확실하다)

    @property
    def key(self) -> str:
        return f'{self.owner_repo}/{self.name}'

    @property
    def doc_id(self) -> str:
        return hashlib.sha256(self.key.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Skill:
    """상세 페이지까지 읽은 스킬. 근거 자료(요약·SKILL.md)와 코드가 직접 넣을 사실(설치 명령·숫자·감사)을 담는다."""

    ref: SkillRef
    boards: tuple[str, ...] = ()
    category: str = ''
    headline: str = ''
    bullets: tuple[str, ...] = ()
    install_cmd: str = ''
    installs: int = 0
    stars: int = 0
    first_seen: str = ''
    audits: dict[str, str] = field(default_factory=dict)
    verified_org: bool = False
    skill_md: str = ''

    @property
    def key(self) -> str:
        return self.ref.key

    @property
    def name(self) -> str:
        return self.ref.name

    @property
    def url(self) -> str:
        return f'{BASE}/{self.ref.owner_repo}/{self.ref.name}'

    @property
    def github_url(self) -> str:
        return f'https://github.com/{self.ref.owner_repo}'

    @property
    def audit_problems(self) -> tuple[str, ...]:
        return tuple(f'{name}: {status}' for name, status in self.audits.items() if status.lower() != GOOD_AUDIT)

    @property
    def audit_warnings(self) -> tuple[str, ...]:
        return tuple(f'{name}: {status}' for name, status in self.audits.items() if status.lower() == WARN_AUDIT)

    @property
    def audit_failures(self) -> tuple[str, ...]:
        """Pass도 Warn도 아닌 결과 (Fail, 모르는 상태)."""
        return tuple(f'{name}: {status}' for name, status in self.audits.items() if status.lower() not in (GOOD_AUDIT, WARN_AUDIT))

    @property
    def audits_ok(self) -> bool:
        """감사 결과가 있고 Fail·모르는 상태가 없을 때 True (경고 Warn은 허용, 결과가 없으면 검증 못 한 스킬이라 믿지 않는다)."""
        return bool(self.audits) and not self.audit_failures

    @property
    def audits_clean(self) -> bool:
        """감사 결과가 있고 전부 Pass일 때만 True (결과가 없으면 검증 못 한 스킬이라 믿지 않는다)."""
        return bool(self.audits) and not self.audit_problems


def parse_count(text: str) -> int:
    """'39.8K' → 39800, '1.2M' → 1200000, 못 읽으면 0."""
    match = _COUNT_RE.match(text or '')
    if not match:
        return 0
    return int(round(float(match.group(1)) * _UNITS[match.group(2).lower()]))


def _text(fragment: str) -> str:
    return re.sub(r'\s+', ' ', html_lib.unescape(_TAG_RE.sub(' ', fragment))).strip()


def parse_list(html: str, board: str) -> list[SkillRef]:
    """리더보드 HTML에서 (순위, 스킬, 저장소, 숫자)를 읽는다. 웹사이트 출처(/site/…)는 GitHub 검증이 안 돼 뺀다."""
    refs, seen = [], set()
    for match in _ROW_RE.finditer(html):
        path, body = match.group('path'), match.group('body')
        owner, repo, name = path.split('/')
        if owner == 'site':
            continue
        head, _, tail = body.partition('<h3')
        rank_spans = re.findall(r'<span[^>]*>\s*(\d+)\s*</span>', head)
        metric_spans = re.findall(r'<span[^>]*>\s*([0-9][0-9.,]*[KkMm]?)\s*</span>', tail.partition('</p>')[2])
        rank = int(rank_spans[0]) if rank_spans else len(refs) + 1
        metric = parse_count(metric_spans[-1]) if metric_spans else 0
        key = f'{owner}/{repo}/{name}'
        if key in seen:
            continue
        seen.add(key)
        refs.append(SkillRef(f'{owner}/{repo}', name, rank, metric, board))
    return sorted(refs, key=lambda r: r.rank)


def fetch_candidates(get: HttpGet) -> list[SkillRef]:
    """Trending과 Hot을 합쳐 중복 없이 돌려준다 (두 보드에 다 있으면 boards에 둘 다 적는다). 한 보드가 죽어도 나머지로 진행."""
    merged: dict[str, SkillRef] = {}
    boards_of: dict[str, list[str]] = {}
    errors = []
    for board, url in BOARD_URLS.items():
        try:
            refs = parse_list(get(url), board)
        except Exception as e:  # noqa: BLE001 - 네트워크·파싱 오류는 보드 단위로 격리한다
            errors.append(f'{board}: {e.__class__.__name__}')
            continue
        for ref in refs:
            merged.setdefault(ref.key, ref)
            boards_of.setdefault(ref.key, []).append(board)
    if not merged:
        raise SourceError('skills.sh 목록을 읽지 못했습니다: ' + ', '.join(errors or ['빈 목록']))
    return [replace(ref, boards=tuple(boards_of[key])) for key, ref in merged.items()]


def _between(html: str, start: str, end: str) -> str:
    i = html.find(start)
    if i < 0:
        return ''
    j = html.find(end, i + len(start))
    return html[i + len(start):j if j >= 0 else len(html)]


def _stat(sidebar: str, label: str) -> str:
    """사이드바 '라벨 → 값' 칸의 글자."""
    return _text(_between(sidebar, f'<span>{label}</span>', '</div></div>'))


def _sidebar(page: str) -> str:
    """오른쪽 사이드바(설치 수·저장소·별·감사). SKILL.md 본문은 사용자가 쓴 글이라 같은 표식을 흉내 낼 수 있으니
    페이지에서 마지막으로 나오는 '설치' 칸부터 읽는다 (사이드바는 본문 뒤에 있다). 못 찾으면 빈 문자열 → 값 없음."""
    i = page.rfind('<span>Installs</span>')
    return page[i:] if i >= 0 else ''


_AUDIT_ROW_RE = re.compile(
    r'<span class="text-sm font-medium[^>]*>(.*?)</span><span class="text-xs font-mono uppercase[^>]*>(.*?)</span>', re.S)


def parse_audits(sidebar: str) -> dict[str, str]:
    """보안 감사 표. 링크 수와 읽은 행 수가 다르거나 이름이 겹치면 믿지 않고 빈 값(=미통과)으로 둔다."""
    block = _between(sidebar, 'Security Audits</div>', '</main>')
    rows = [(_text(n), _text(st)) for n, st in _AUDIT_ROW_RE.findall(block)]
    if len(rows) != block.count('/security/') or len({n for n, _ in rows}) != len(rows):
        return {}
    return dict(rows)


def safe_install_command(raw: str, ref: SkillRef) -> str:
    """페이지의 설치 명령이 링크(저장소·스킬 이름)와 정확히 같을 때만 쓴다. 캡션·DM에 실려 사람들이 터미널에 붙여 넣기 때문이다."""
    command = html_lib.unescape(raw).strip()
    match = _INSTALL_RE.match(command)
    if match and match.group('repo') == ref.owner_repo and match.group('skill') == ref.name:
        return command
    return ''


def parse_detail(html: str, ref: SkillRef) -> Skill:
    page = _STRIP_RE.sub('', html)
    install = re.search(r'npx skills add [^<"]+', page)
    category = re.search(r'href="/topic/[^"]*"[^>]*>([^<]+)</a>', page)
    summary = _between(page, '>Summary</div>', '<div class="bg-background">')
    headline = re.search(r'<p><strong>(.*?)</strong></p>', summary, re.S)
    bullets = tuple(_text(li) for li in re.findall(r'<li>(.*?)</li>', summary, re.S))
    sidebar = _sidebar(page)
    skill_md = _between(page, '<span>SKILL.md</span>', '<section')
    if not skill_md:
        skill_md = _between(page, '<span>SKILL.md</span>', '<span>Installs</span>')
    return Skill(
        ref=ref, boards=ref.boards or (ref.board,),
        category=html_lib.unescape(category.group(1)).strip() if category else '',
        headline=_text(headline.group(1)) if headline else '',
        bullets=bullets,
        install_cmd=safe_install_command(install.group(0), ref) if install else '',
        installs=parse_count(_stat(sidebar, 'Installs')),
        stars=parse_count(_stat(sidebar, 'GitHub Stars')),
        first_seen=_stat(sidebar, 'First Seen'),
        audits=parse_audits(sidebar),
        verified_org='title="Verified organization on GitHub"' in _between(sidebar, '<span>Repository</span>', '<span>GitHub Stars</span>'),
        skill_md=_text(skill_md)[:MAX_SKILL_MD_CHARS],
    )


def fetch_detail(get: HttpGet, ref: SkillRef) -> Skill:
    return parse_detail(get(f'{BASE}/{ref.owner_repo}/{ref.name}'), ref)
