"""정보마스터 블로그 관리 화면 (맥북 전용, http://127.0.0.1:8501).

실행: `관리화면 열기.command` 더블클릭 또는
    .venv/bin/streamlit run ui/app.py
"""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime, time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import providers, runlog, schedule
from src.history import LEGACY_SLOT_TIME
from ui import catalog, envfile
from ui.github import DEFAULT_REPO, GitHubClient, GitHubError, gh_token, key_log_lines

PREVIEW_CACHE = ROOT / 'output' / 'ui_previews'
MODE_LABELS = {'auto': '정기 자동', 'schedule': '다음 시각 예약', 'draft': '초안 저장', 'now': '즉시 발행', 'preview': '미리보기'}
EVENT_LABELS = {'schedule': '⏰ 정기', 'workflow_dispatch': '🖱️ 수동'}
OUTCOME_LABELS = {'success': '✅ 성공', 'skipped': '⏭️ 건너뜀', 'failed': '❌ 실패'}
STATUS_LABELS = {'SCHEDULED': '⏰ 예약됨', 'LIVE': '🟢 공개', 'DRAFT': '📝 초안', 'PREVIEW': '👀 미리보기'}
DEFAULT_TIME_SLOTS = (time(8, 0), time(12, 0), time(20, 0))
CUSTOM_MODEL = '직접 입력…'


# --- 데이터 ---

@st.cache_resource
def get_client() -> GitHubClient:
    return GitHubClient(DEFAULT_REPO, gh_token())


@st.cache_data(ttl=30, show_spinner=False)
def load_variables(_client: GitHubClient) -> dict[str, str]:
    return _client.list_variables()


@st.cache_data(ttl=30, show_spinner=False)
def load_secrets(_client: GitHubClient) -> dict[str, str]:
    return _client.list_secrets()


@st.cache_data(ttl=20, show_spinner=False)
def load_history_rows(_client: GitHubClient) -> list[dict]:
    text = _client.read_file('data/history.json')
    rows = json.loads(text) if text else []
    # 슬롯 도입 전 기록은 20시 발행분으로 간주 (src/history.py와 같은 규칙)
    normalized = [{**r, 'slot': r.get('slot') or f"{r['publish_date']}T{LEGACY_SLOT_TIME}"} for r in rows]
    return list(reversed(normalized))


@st.cache_data(ttl=20, show_spinner=False)
def load_run_records(_client: GitHubClient) -> list[dict]:
    text = _client.read_file('data/runs.jsonl')
    return runlog.parse_runs(text) if text else []


@st.cache_data(ttl=10, show_spinner=False)
def load_gh_runs(_client: GitHubClient) -> list[dict]:
    return _client.list_runs()


@st.cache_data(ttl=600, show_spinner='모델 목록을 불러오는 중…')
def load_catalog(provider: str, key: str) -> catalog.Catalog:
    return catalog.fetch_catalog(provider, key)


def refresh() -> None:
    st.cache_data.clear()
    st.rerun()


def fmt_time(value: str) -> str:
    if not value:
        return ''
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(schedule.KST).strftime('%m/%d %H:%M')
    except ValueError:
        return value


def run_state(run: dict) -> str:
    if run['status'] != 'completed':
        return '⏳ 실행 중' if run['status'] == 'in_progress' else '🕐 대기 중'
    return {'success': '✅ 완료', 'failure': '❌ 실패', 'cancelled': '⛔ 취소'}.get(run['conclusion'] or '', run['conclusion'] or '')


def current_times(variables: dict[str, str]) -> tuple[schedule.Time, ...]:
    try:
        return schedule.parse_times(variables.get('PUBLISH_TIMES'))
    except schedule.ScheduleError:
        return schedule.parse_times(None)


# --- 페이지: 대시보드 ---

def dispatch(client: GitHubClient, mode: str, at: str | None = None) -> None:
    client.dispatch(mode, at)
    st.cache_data.clear()
    st.success(f'"{MODE_LABELS[mode]}" 실행을 요청했습니다. 1~3분 뒤 아래 목록에서 결과를 확인하세요.')


def page_dashboard(client: GitHubClient) -> None:
    st.title('📰 정보마스터 대시보드')
    variables = load_variables(client)
    rows = load_history_rows(client)
    times = current_times(variables)
    paused = variables.get('PAUSED', 'false').lower() == 'true'
    ai = providers.load_ai_settings(variables)
    now = datetime.now(schedule.KST)

    upcoming = [r for r in rows if r.get('status') == 'SCHEDULED' and r.get('slot', '') >= schedule.slot_key(now)]
    done = {r.get('slot') for r in rows}
    next_slot = schedule.next_open_slot(now, times, done)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric('발행 시각 (매일)', schedule.format_times(times).replace(',', ' · '))
    c2.metric('자동 발행', '⏸️ 일시정지' if paused else '▶️ 켜짐')
    c3.metric('예약된 글', f'{len(upcoming)}편')
    c4.metric('글쓰기 AI', f'{providers.PROVIDER_LABELS[ai.text_provider]}')
    if upcoming:
        first = min(upcoming, key=lambda r: r['slot'])
        st.info(f"다음 공개: **{first['title']}** · {first['slot'].replace('T', ' ')}")
    if next_slot:
        st.caption(f'다음으로 자동 준비할 시각: {schedule.slot_key(next_slot).replace("T", " ")} (약 6시간 전부터 준비)')

    st.subheader('바로 실행')
    b1, b2, b3, b4 = st.columns(4)
    if b1.button('👀 미리보기 만들기', width='stretch', help='블로그에 올리지 않고 글과 이미지만 만들어 봅니다'):
        dispatch(client, 'preview')
    if b2.button('📝 초안으로 저장', width='stretch', help='블로그에 초안으로만 저장합니다 (공개 안 됨)'):
        dispatch(client, 'draft')
    if b3.button('⏰ 다음 시각에 예약', width='stretch', help='아직 준비 안 된 가장 가까운 발행 시각에 1편 예약'):
        dispatch(client, 'schedule')
    with b4:
        confirm = st.checkbox('즉시 공개에 동의', help='체크해야 즉시 발행 버튼이 켜집니다')
        if st.button('🚀 지금 즉시 발행', type='primary', width='stretch', disabled=not confirm):
            dispatch(client, 'now')

    with st.expander('원하는 시각에 1편 예약하기'):
        picked = st.time_input('예약 시각 (KST)', value=time(21, 0), step=300)
        if st.button('이 시각으로 예약'):
            dispatch(client, 'schedule', picked.strftime('%H:%M'))

    st.subheader('최근 실행')
    if st.button('🔄 새로고침'):
        refresh()
    runs = load_gh_runs(client)[:8]
    if not runs:
        st.write('아직 실행 기록이 없습니다.')
    for run in runs:
        st.markdown(f"{run_state(run)} · {EVENT_LABELS.get(run['event'], run['event'])} · "
                    f"{fmt_time(run['created_at'])} · [GitHub에서 보기]({run['url']})")


# --- 페이지: 실행 기록 ---

def show_preview(client: GitHubClient, run_id: int) -> None:
    page = client.download_preview(run_id, PREVIEW_CACHE / str(run_id))
    if page is None:
        st.warning('미리보기 파일이 없습니다 (14일이 지났거나 실패한 실행).')
        return
    html = page.read_text(encoding='utf-8')
    hero = page.parent / 'hero.webp'
    if hero.exists():
        data_uri = 'data:image/webp;base64,' + base64.b64encode(hero.read_bytes()).decode()
        html = html.replace('src="hero.webp"', f'src="{data_uri}"')
    st.components.v1.html(html, height=1400, scrolling=True)


def page_runs(client: GitHubClient) -> None:
    st.title('📜 실행 기록')
    if st.button('🔄 새로고침'):
        refresh()
    records = load_run_records(client)
    gh_runs = {str(r['id']): r for r in load_gh_runs(client)}

    st.subheader('글 작업 기록')
    if not records:
        st.write('아직 기록이 없습니다. (새 기록 방식 적용 이후 실행분부터 쌓입니다)')
    table = []
    for rec in records:
        posts = rec.get('posts') or []
        table.append({
            '시작': fmt_time(rec.get('started_at', '')),
            '방식': EVENT_LABELS.get(rec.get('trigger'), rec.get('trigger')),
            '모드': MODE_LABELS.get(rec.get('mode'), rec.get('mode')),
            '결과': OUTCOME_LABELS.get(rec.get('outcome'), rec.get('outcome')),
            '글': ' / '.join(p['title'] for p in posts) or rec.get('message', ''),
            'AI': f"{rec.get('text_provider', '')} {rec.get('writer_model', '')}",
            '로그': rec.get('run_url', ''),
        })
    if table:
        st.dataframe(table, width='stretch', hide_index=True,
                     column_config={'로그': st.column_config.LinkColumn('로그', display_text='열기')})

    st.subheader('로그 · 미리보기 보기')
    options = {f"{fmt_time(r['created_at'])} · {EVENT_LABELS.get(r['event'], r['event'])} · {run_state(r)}": r['id']
               for r in gh_runs.values()}
    if not options:
        return
    choice = st.selectbox('실행 선택', list(options))
    run_id = options[choice]
    record = next((r for r in records if str(r.get('run_id')) == str(run_id)), None)
    if record and record.get('mode') == 'preview' and st.button('👀 이 실행의 미리보기 열기'):
        show_preview(client, run_id)
    if st.button('📄 로그 불러오기'):
        try:
            log = client.run_log(run_id)
        except GitHubError as e:
            st.warning(str(e))
            return
        st.code('\n'.join(key_log_lines(log)) or '(파이프라인 진행 기록 없음: 준비할 발행 시각이 없어 건너뛴 실행)',
                language='text')
        with st.expander('전체 로그'):
            st.code(log[-20000:], language='text')


# --- 페이지: 발행 글 ---

def page_posts(client: GitHubClient) -> None:
    st.title('📰 발행 글')
    rows = load_history_rows(client)
    if not rows:
        st.write('아직 발행한 글이 없습니다.')
        return
    st.dataframe(
        [{
            '발행 시각': (r.get('slot') or r.get('publish_date', '')).replace('T', ' '),
            '상태': STATUS_LABELS.get(r.get('status'), r.get('status')),
            '제목': r.get('title'),
            '라벨': ', '.join(r.get('labels', [])),
            '글 주소': r.get('url') or None,
            '원문': r.get('source_url'),
        } for r in rows],
        width='stretch', hide_index=True,
        column_config={
            '글 주소': st.column_config.LinkColumn('글 주소', display_text='열기'),
            '원문': st.column_config.LinkColumn('원문', display_text='원문'),
        },
    )


# --- 페이지: 발행 설정 ---

def page_schedule(client: GitHubClient) -> None:
    st.title('⏰ 발행 설정')
    variables = load_variables(client)
    times = current_times(variables)

    count = st.radio('하루 발행 횟수', [1, 2, 3], index=len(times) - 1, horizontal=True)
    if count >= 3:
        st.warning('하루 3편 이상은 Google이 "대량 생성 콘텐츠"로 볼 위험이 커집니다. 품질이 안정된 뒤에 늘리세요.')
    cols = st.columns(count)
    picked = []
    for i in range(count):
        default = time(*times[i]) if i < len(times) else DEFAULT_TIME_SLOTS[i]
        picked.append(cols[i].time_input(f'{i + 1}번째 발행 시각', value=default, step=300, key=f'slot{i}'))

    paused = st.toggle('자동 발행 일시정지', value=variables.get('PAUSED', 'false').lower() == 'true',
                       help='켜면 정기 자동 발행을 멈춥니다. 수동 실행 버튼은 계속 쓸 수 있습니다.')
    footer = st.text_input('모든 글 하단 링크', value=variables.get('FOOTER_LINK_URL',
                                                             'https://link.inpock.co.kr/shoperwoody'))

    if st.button('💾 저장', type='primary'):
        try:
            value = schedule.format_times(schedule.parse_times(','.join(t.strftime('%H:%M') for t in picked)))
        except schedule.ScheduleError as e:
            st.error(str(e))
            return
        if len(value.split(',')) < count:
            st.error('같은 시각이 중복되어 있습니다.')
            return
        if footer and not footer.startswith('https://'):
            st.error('하단 링크는 https://로 시작해야 합니다.')
            return
        client.set_variables({'PUBLISH_TIMES': value, 'PAUSED': 'true' if paused else 'false',
                              'FOOTER_LINK_URL': footer})
        st.cache_data.clear()
        st.success(f'저장했습니다. 매일 {value.replace(",", ", ")}에 발행합니다. (다음 정기 점검부터 적용)')


# --- 페이지: AI 설정 ---

def model_picker(label: str, options: tuple[str, ...], current: str, key: str) -> str:
    choices = list(options)
    if current and current not in choices:
        choices.insert(0, current)
    choices.append(CUSTOM_MODEL)
    selected = st.selectbox(label, choices, index=choices.index(current) if current in choices else 0, key=key)
    if selected == CUSTOM_MODEL:
        return st.text_input(f'{label} 이름 직접 입력', value=current, key=f'{key}_custom').strip()
    return selected


def provider_catalog(provider: str, local_env: dict[str, str]) -> catalog.Catalog:
    key = local_env.get(providers.KEY_ENV[provider], '')
    if not key:
        st.warning(f'{providers.PROVIDER_LABELS[provider]} 키가 이 맥북에 없어 모델 목록을 불러올 수 없습니다. '
                   '"API 키" 메뉴에서 먼저 등록하거나 모델 이름을 직접 입력하세요.')
        return catalog.Catalog((), ())
    try:
        return load_catalog(provider, key)
    except Exception as e:  # noqa: BLE001 - 회사마다 예외가 달라 화면 메시지로만 알린다
        st.error(f'모델 목록 조회 실패 ({e.__class__.__name__}). 키를 확인하세요.')
        return catalog.Catalog((), ())


def page_ai(client: GitHubClient) -> None:
    st.title('🧠 AI 설정')
    st.caption('글쓰기 모델은 성능 좋은 모델, 검수 모델은 빠르고 저렴한 모델을 추천합니다.')
    variables = load_variables(client)
    secrets = load_secrets(client)
    local_env = envfile.read_env()
    current = providers.load_ai_settings(variables)

    st.subheader('글쓰기 · 검수 AI')
    text_provider = st.selectbox(
        'AI 회사', providers.TEXT_PROVIDERS, index=providers.TEXT_PROVIDERS.index(current.text_provider),
        format_func=providers.PROVIDER_LABELS.get,
    )
    defaults = providers.DEFAULT_TEXT_MODELS[text_provider]
    same = text_provider == current.text_provider
    text_catalog = provider_catalog(text_provider, local_env)
    writer_model = model_picker('글쓰기 모델', text_catalog.text_models,
                                current.writer_model if same else defaults['writer'], 'writer')
    fast_model = model_picker('선별·검수 모델', text_catalog.text_models,
                              current.fast_model if same else defaults['fast'], 'fast')

    st.subheader('대표 이미지 AI')
    image_provider = st.selectbox(
        '이미지 AI 회사', providers.IMAGE_PROVIDERS, index=providers.IMAGE_PROVIDERS.index(current.image_provider),
        format_func=providers.PROVIDER_LABELS.get, help='Claude는 이미지 생성을 지원하지 않습니다.',
    )
    image_catalog = provider_catalog(image_provider, local_env) if image_provider != text_provider else text_catalog
    image_model = model_picker(
        '이미지 모델', image_catalog.image_models,
        current.image_model if image_provider == current.image_provider else providers.DEFAULT_IMAGE_MODELS[
            image_provider], 'image')
    image_quality = st.radio('이미지 품질', providers.IMAGE_QUALITIES,
                             index=providers.IMAGE_QUALITIES.index(current.image_quality), horizontal=True,
                             help='high는 선명하지만 비용이 몇 배 비쌉니다. OpenAI에만 적용됩니다.')

    missing = [providers.KEY_ENV[p] for p in {text_provider, image_provider} if providers.KEY_ENV[p] not in secrets]
    if missing:
        st.error(f'GitHub에 {", ".join(missing)}가 등록되어 있지 않습니다. "API 키" 메뉴에서 먼저 등록하세요.')

    if st.button('💾 AI 설정 저장', type='primary', disabled=bool(missing) or not writer_model or not fast_model):
        values = {'TEXT_PROVIDER': text_provider, 'WRITER_MODEL': writer_model, 'FAST_MODEL': fast_model,
                  'IMAGE_PROVIDER': image_provider, 'IMAGE_MODEL': image_model, 'IMAGE_QUALITY': image_quality}
        client.set_variables(values)
        envfile.update_env(values)
        st.cache_data.clear()
        st.success('저장했습니다. 대시보드에서 "미리보기 만들기"로 새 AI의 글을 먼저 확인해 보세요.')


# --- 페이지: API 키 ---

def page_keys(client: GitHubClient) -> None:
    st.title('🔑 API 키')
    st.caption('키는 확인을 통과해야 저장됩니다. 저장하면 이 맥북(.env)과 GitHub Secrets 두 곳에 들어갑니다. '
               '저장된 키는 다시 화면에 보이지 않습니다.')
    secrets = load_secrets(client)
    local_env = envfile.read_env()
    for provider in providers.TEXT_PROVIDERS:
        name = providers.KEY_ENV[provider]
        with st.container(border=True):
            st.markdown(f'**{providers.PROVIDER_LABELS[provider]}** · `{name}`')
            local = '✅ 있음' if local_env.get(name) else '❌ 없음'
            remote = f'✅ 등록됨 ({fmt_time(secrets[name])})' if name in secrets else '❌ 없음'
            st.write(f'맥북: {local} · GitHub: {remote}')
            with st.form(f'form_{provider}', clear_on_submit=True):
                value = st.text_input('새 키', type='password', placeholder='여기에 붙여넣기')
                if st.form_submit_button('확인 후 저장'):
                    ok, message = catalog.check_key(provider, value)
                    if not ok:
                        st.error(message)
                    else:
                        envfile.update_env({name: value.strip()})
                        client.set_secret(name, value.strip())
                        st.cache_data.clear()
                        st.success(f'{message} · 저장 완료')


# --- 진입점 ---

PAGES = {
    '🏠 대시보드': page_dashboard,
    '📜 실행 기록': page_runs,
    '📰 발행 글': page_posts,
    '⏰ 발행 설정': page_schedule,
    '🧠 AI 설정': page_ai,
    '🔑 API 키': page_keys,
}


def main() -> None:
    st.set_page_config(page_title='정보마스터 관리', page_icon='📰', layout='wide')
    with st.sidebar:
        st.header('정보마스터 관리')
        page = st.radio('메뉴', list(PAGES), label_visibility='collapsed')
        st.divider()
        st.caption(f'저장소: {DEFAULT_REPO}')
        st.link_button('블로그 열기', 'https://www.woodyway88.com')
    try:
        client = get_client()
        PAGES[page](client)
    except GitHubError as e:
        st.error(str(e))
    except (providers.ProviderConfigError, schedule.ScheduleError) as e:
        st.error(f'설정 오류: {e}')


main()
