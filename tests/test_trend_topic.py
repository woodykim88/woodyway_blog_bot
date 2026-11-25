"""화제 우선 글감: 구글 트렌드 급상승 검색어 → 블로그에 맞는 검색어 고르기 → 기사 모으기."""
from __future__ import annotations

from datetime import datetime, timezone

from src import trend_topic, trends
from src.models import Candidate
from tests.helpers import fake_llm

NOW = datetime(2026, 11, 26, 5, 0, tzinfo=timezone.utc)

RSS = """<?xml version="1.0"?><rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>한전 고객정보 유출 대책</title><ht:approx_traffic>2,000+</ht:approx_traffic>
 <ht:news_item><ht:news_item_title>한전, 고객정보 유출 피해 보상</ht:news_item_title>
  <ht:news_item_url>https://www.fnnews.com/news/1</ht:news_item_url><ht:news_item_source>파이낸셜뉴스</ht:news_item_source></ht:news_item>
 <ht:news_item><ht:news_item_title>한전 유출 조회 방법</ht:news_item_title>
  <ht:news_item_url>https://www.yna.co.kr/view/2</ht:news_item_url><ht:news_item_source>연합뉴스</ht:news_item_source></ht:news_item>
</item>
<item><title>성시경</title><ht:approx_traffic>500+</ht:approx_traffic></item>
<item><title>Bitcoin</title><ht:approx_traffic>200+</ht:approx_traffic></item>
</channel></rss>"""


def test_parse_items_keeps_traffic_and_news():
    items = trends.parse_google_trend_items(RSS)
    assert [i.keyword for i in items] == ['한전 고객정보 유출 대책', '성시경']
    assert items[0].traffic == 2000
    assert items[0].news[0] == trends.NewsLink('한전, 고객정보 유출 피해 보상', 'https://www.fnnews.com/news/1', '파이낸셜뉴스')
    assert items[1].traffic == 500 and items[1].news == ()


def test_parse_traffic_handles_odd_values():
    assert trends.parse_traffic('1,000+') == 1000
    assert trends.parse_traffic('') == 0
    assert trends.parse_traffic('많음') == 0


def _choice(index, score, query='한전 고객정보 유출 대책', angle='내 정보 유출 조회와 보상'):
    return trend_topic.TrendChoice(index=index, score=score, reason='r', search_query=query, angle=angle)


def test_pick_trends_filters_low_scores_and_sorts():
    items = trends.parse_google_trend_items(RSS)
    llm, _ = fake_llm({'TrendSelection': [trend_topic.TrendSelection(choices=[
        _choice(1, 2, '성시경'), _choice(0, 8), _choice(9, 10, '없는 번호')])]})
    picks = trend_topic.pick_trends(llm, 'm', items, ('지난 글',))
    assert [p.item.keyword for p in picks] == ['한전 고객정보 유출 대책']
    assert picks[0].angle == '내 정보 유출 조회와 보상'


def test_pick_trends_forces_keyword_into_query():
    items = trends.parse_google_trend_items(RSS)
    llm, _ = fake_llm({'TrendSelection': [trend_topic.TrendSelection(choices=[_choice(0, 9, '전기요금 정보 유출')])]})
    assert trend_topic.pick_trends(llm, 'm', items, ())[0].search_query == '한전 고객정보 유출 대책'


def test_pick_trends_skips_llm_when_no_items():
    llm, client = fake_llm({})
    assert trend_topic.pick_trends(llm, 'm', (), ()) == []
    assert client.requests == []


ROBOTS_BLOCK_AI = 'User-agent: GPTBot\nDisallow: /\n'
ROBOTS_OPEN = 'User-agent: *\nDisallow: /admin\n'


def test_robots_blocks_ai_crawlers_and_failures():
    pages = {'https://www.yna.co.kr/robots.txt': ROBOTS_BLOCK_AI, 'https://www.fnnews.com/robots.txt': ROBOTS_OPEN}

    def get(url):
        if url not in pages:
            raise trend_topic.requests.RequestException('x')
        return pages[url]

    check = trend_topic.RobotsCheck(get)
    assert check.allows('https://www.fnnews.com/news/1')
    assert not check.allows('https://www.yna.co.kr/view/2')
    assert not check.allows('https://unknown.example/a')


def test_gather_sources_uses_allowed_news_and_matching_pool():
    item = trends.parse_google_trend_items(RSS)[0]
    pick = trend_topic.TrendPick(item, 8, '한전 고객정보 유출 대책', 'a')
    pool = [
        Candidate('한전 고객정보 유출 대책 발표', 'https://www.hankyung.com/a', 's', NOW, '한국경제', True),
        Candidate('코스피 마감', 'https://www.hankyung.com/b', 's', NOW, '한국경제', True),
    ]
    allows = lambda url: 'yna' not in url  # noqa: E731
    found = trend_topic.gather_sources(pick, pool, allows, NOW)
    assert [c.link for c in found] == ['https://www.fnnews.com/news/1', 'https://www.hankyung.com/a']
    assert found[0].direct and found[0].source == '파이낸셜뉴스'


# --- 파이프라인 연결 ---
from src import main as pipeline  # noqa: E402
from src.llm import LLMError  # noqa: E402
from tests.helpers import make_article, make_draft, make_entry  # noqa: E402

MODELS = pipeline.Models(writer='w', fast='f')


def _setup(monkeypatch, picks, articles):
    monkeypatch.setattr(pipeline.trends, 'fetch_trend_items', lambda get: trends.parse_google_trend_items(RSS))
    monkeypatch.setattr(pipeline.trend_topic, 'pick_trends', lambda *a, **k: picks)
    monkeypatch.setattr(pipeline.collector, 'fetch_candidates', lambda *a, **k: [])
    monkeypatch.setattr(pipeline.trend_topic.RobotsCheck, 'allows', lambda self, url: True)
    monkeypatch.setattr(pipeline.fetcher, 'fetch_article', lambda get, c: articles.get(c.link))
    monkeypatch.setattr(pipeline.fetcher, 'with_references', lambda get, a, rel: a)


def test_compose_trend_post_writes_with_keyword_as_target_query(monkeypatch):
    item = trends.parse_google_trend_items(RSS)[0]
    pick = trend_topic.TrendPick(item, 9, item.keyword, '유출 조회와 보상')
    _setup(monkeypatch, [pick], {'https://www.fnnews.com/news/1': make_article()})
    seen = {}

    def fake_draft(llm, models, article, theme, past, is_alive, category, target_query='', day=None, kind_rules=''):
        seen.update(query=target_query, rules=kind_rules, category=category)
        return make_draft(labels=['생활경제'])

    monkeypatch.setattr(pipeline, 'draft_article', fake_draft)
    composed = pipeline.compose_trend_post(*fake_llm({})[:1], MODELS, NOW, (), get=lambda url: '')
    assert composed is not None and composed.category == pipeline.topics.MONEY
    assert seen['query'] == '한전 고객정보 유출 대책'
    assert '화제 글 규칙' in seen['rules'] and '유출 조회와 보상' in seen['rules']


def test_compose_trend_post_none_without_picks_or_articles(monkeypatch):
    _setup(monkeypatch, [], {})
    assert pipeline.compose_trend_post(*fake_llm({})[:1], MODELS, NOW, (), get=lambda url: '') is None
    item = trends.parse_google_trend_items(RSS)[0]
    _setup(monkeypatch, [trend_topic.TrendPick(item, 9, item.keyword, 'a')], {})
    assert pipeline.compose_trend_post(*fake_llm({})[:1], MODELS, NOW, (), get=lambda url: '') is None


def test_already_written_keywords_are_dropped(monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline.trends, 'fetch_trend_items', lambda get: trends.parse_google_trend_items(RSS))

    def fake_pick(llm, model, items, recent):
        seen['items'] = [i.keyword for i in items]
        return []

    monkeypatch.setattr(pipeline.trend_topic, 'pick_trends', fake_pick)
    past = (make_entry(title='한전 고객정보 유출 대책, 조회 방법'),)
    pipeline.compose_trend_post(*fake_llm({})[:1], MODELS, NOW, past, get=lambda url: '')
    assert seen['items'] == ['성시경']


def test_evening_post_tries_trend_first_then_falls_back(monkeypatch):
    order = []
    monkeypatch.setattr(pipeline, 'compose_trend_post', lambda *a, **k: order.append('trend') or None)
    monkeypatch.setattr(pipeline, 'compose_howto_post', lambda *a, **k: order.append('howto') or 'HOWTO')
    assert pipeline.compose_evening_post(*fake_llm({})[:1], MODELS, NOW, (), trend=True) == 'HOWTO'
    assert order == ['trend', 'howto']
    order.clear()
    pipeline.compose_evening_post(*fake_llm({})[:1], MODELS, NOW, ())
    assert order == ['howto']


def test_evening_post_survives_any_trend_error(monkeypatch):
    def boom(*a, **k):
        raise LLMError('x') if k.get('day') else AttributeError('y')

    monkeypatch.setattr(pipeline, 'compose_trend_post', boom)
    monkeypatch.setattr(pipeline, 'compose_howto_post', lambda *a, **k: 'HOWTO')
    assert pipeline.compose_evening_post(*fake_llm({})[:1], MODELS, NOW, (), trend=True) == 'HOWTO'


def test_trend_first_flag_is_opt_in():
    assert not pipeline.trend_first({})
    assert pipeline.trend_first({'TREND_FIRST': 'on'})


BING = """<?xml version="1.0"?><rss xmlns:News="https://www.bing.com/news/search?q=x&amp;format=rss" version="2.0"><channel>
<item><title>한전, 정보유출 고객에 최대 1천만원 보상</title>
 <link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2fwww.nocutnews.co.kr%2fnews%2f6612182&amp;c=1</link>
 <News:Source>노컷뉴스</News:Source></item>
<item><title>한전 2차피해 막는다</title><link>https://www.msn.com/ko-kr/news/other/a</link><News:Source>뉴스핌 on MSN</News:Source></item>
<item><title>한전 2차 피해 예방</title><link>https://www.fnnews.com/news/2</link><News:Source>파이낸셜뉴스</News:Source></item>
</channel></rss>"""


def test_parse_bing_news_unwraps_links_and_drops_msn():
    links = trend_topic.parse_bing_news(BING)
    assert links == (
        trends.NewsLink('한전, 정보유출 고객에 최대 1천만원 보상', 'https://www.nocutnews.co.kr/news/6612182', '노컷뉴스'),
        trends.NewsLink('한전 2차 피해 예방', 'https://www.fnnews.com/news/2', '파이낸셜뉴스'),
    )


def test_search_news_survives_failures():
    def get(url):
        raise trend_topic.requests.RequestException('x')

    assert trend_topic.search_news(get, '한전') == ()


def test_gather_sources_adds_searched_news_and_token_matches():
    item = trends.TrendItem('한전 고객정보 유출 대책', 0)
    pick = trend_topic.TrendPick(item, 8, item.keyword, 'a')
    pool = [Candidate('한전, 고객정보 유출 2차 피해 보상', 'https://www.hankyung.com/a', 's', NOW, '한국경제', True),
            Candidate('한전 주가 마감', 'https://www.hankyung.com/b', 's', NOW, '한국경제', True)]
    extra = trend_topic.parse_bing_news(BING)
    found = trend_topic.gather_sources(pick, pool, lambda url: True, NOW, extra_news=extra, limit=5)
    assert [c.link for c in found] == ['https://www.nocutnews.co.kr/news/6612182', 'https://www.fnnews.com/news/2',
                                       'https://www.hankyung.com/a']
