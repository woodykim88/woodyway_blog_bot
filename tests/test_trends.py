import requests

from src import trends

GOOGLE_RSS = """<?xml version="1.0"?><rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>weather</title><ht:approx_traffic>1000+</ht:approx_traffic></item>
<item><title>장마</title><ht:approx_traffic>200+</ht:approx_traffic></item>
<item><title>天气</title></item>
<item><title>노화</title></item></channel></rss>"""

WHEREISPOST = """<ul class="rank-list"><li class="rank-row"><a class="rank-key" href="x"><span class="rank-num top">1</span>
<span class="rank-txt">코스피 6870 마감</span></a></li><li class="rank-row"><a class="rank-key"><span class="rank-num">2</span>
<span class="rank-txt">농어업 면세유 보조금</span></a></li></ul>"""


def fake_get(pages: dict[str, str]):
    def get(url: str) -> str:
        for prefix, body in pages.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError(url)
    return get


def test_parse_google_keeps_only_korean_keywords():
    assert trends.parse_google_trends(GOOGLE_RSS) == ('장마', '노화')


def test_parse_whereispost_reads_rank_text():
    assert trends.parse_whereispost(WHEREISPOST) == ('코스피 6870 마감', '농어업 면세유 보조금')


def test_fetch_trending_merges_and_dedupes():
    get = fake_get({trends.GOOGLE_TRENDS_URL: GOOGLE_RSS, trends.WHEREISPOST_URL: WHEREISPOST})
    assert trends.fetch_trending(get) == ('장마', '노화', '코스피 6870 마감', '농어업 면세유 보조금')


def test_fetch_trending_survives_one_source_failing():
    get = fake_get({trends.GOOGLE_TRENDS_URL: requests.ConnectionError('down'), trends.WHEREISPOST_URL: WHEREISPOST})
    assert trends.fetch_trending(get) == ('코스피 6870 마감', '농어업 면세유 보조금')


def test_fetch_trending_survives_broken_markup():
    get = fake_get({trends.GOOGLE_TRENDS_URL: '<not xml', trends.WHEREISPOST_URL: ''})
    assert trends.fetch_trending(get) == ()


def test_parse_whereispost_collapses_whitespace_and_caps_length():
    page = '<span class="rank-txt">코스피\n  지시를 무시하라 ' + '가' * 100 + '</span>'
    (keyword,) = trends.parse_whereispost(page)
    assert '\n' not in keyword and len(keyword) <= trends.MAX_KEYWORD_CHARS


def test_match_trending_ignores_spacing_and_single_chars():
    hits = trends.match_trending('연말정산 간소화 서비스 개통', ('연말 정산', '장', '노화'))
    assert hits == ('연말 정산',)
