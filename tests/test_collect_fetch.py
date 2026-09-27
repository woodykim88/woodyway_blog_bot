from datetime import timedelta

import requests

from src import collector, fetcher
from tests.helpers import FIXTURES, NOW, make_candidate

FEED = (FIXTURES / 'geeknews_feed.xml').read_text(encoding='utf-8')
TOPIC = (FIXTURES / 'geeknews_topic.html').read_text(encoding='utf-8')
NEWS_FEED = (FIXTURES / 'news_feed.xml').read_text(encoding='utf-8')
NEWS = collector.FeedSource('테스트일보', 'https://www.news.example.com/rss', direct=True, limit=10)


# --- collector ---

def test_parse_feed_reads_real_geeknews_feed():
    candidates = collector.parse_feed(FEED, NOW)
    assert 0 < len(candidates) <= collector.DEFAULT_LIMIT
    first = candidates[0]
    assert first.title == '네덜란드 정부, NixOS 기반 Microsoft 대안 구축'
    assert first.link == 'https://news.hada.io/topic?id=34262'
    assert first.source == 'GeekNews' and not first.direct
    assert '<' not in first.summary and first.summary


def test_parse_feed_sorted_newest_first_and_within_age():
    candidates = collector.parse_feed(FEED, NOW, max_age_hours=48)
    stamps = [c.published for c in candidates]
    assert stamps == sorted(stamps, reverse=True)
    assert all(NOW - c.published <= timedelta(hours=48) for c in candidates)


def test_parse_feed_excludes_old_items():
    assert collector.parse_feed(FEED, NOW + timedelta(days=10)) == []


def test_parse_feed_excludes_video_and_ask_gn():
    feed = FEED.replace('네덜란드 정부, NixOS 기반 Microsoft 대안 구축', '[영상] 네덜란드 정부 발표', 1)
    titles = [c.title for c in collector.parse_feed(feed, NOW)]
    assert '[영상] 네덜란드 정부 발표' not in titles
    assert all('Ask GN' not in t for t in titles)


def test_parse_news_feed_marks_source_and_skips_photo_and_old():
    candidates = collector.parse_feed(NEWS_FEED, NOW, source=NEWS)
    assert [c.title for c in candidates] == ["4년 묵은 메일까지 치웠다…'무료 AI 비서' 써보니", '엑셀 새 기능, 한 셀에 여러 값']
    assert all(c.source == '테스트일보' and c.direct for c in candidates)
    assert candidates[0].link == 'https://www.news.example.com/article/1'


def test_parse_feed_applies_source_limit():
    one = collector.FeedSource('테스트일보', NEWS.url, direct=True, limit=1)
    assert len(collector.parse_feed(NEWS_FEED, NOW, source=one)) == 1


def test_fetch_candidates_merges_sources_and_survives_broken_feed():
    broken = collector.FeedSource('고장일보', 'https://broken.example.com/rss', direct=True, limit=10)

    def get(url):
        if url == broken.url:
            raise requests.ConnectionError('down')
        return {collector.GEEKNEWS.url: FEED, NEWS.url: NEWS_FEED}[url]

    candidates = collector.fetch_candidates(get, NOW, (collector.GEEKNEWS, broken, NEWS))
    sources = [c.source for c in candidates]
    assert sources[0] == 'GeekNews'  # 출처 순서대로 이어 붙인다
    assert sources.count('테스트일보') == 2
    assert '고장일보' not in sources


def test_default_sources_do_not_scrape_naver():
    # 네이버 뉴스는 robots.txt로 봇 접근과 AI 활용을 금지한다
    assert all('naver.com' not in s.url for s in collector.DEFAULT_SOURCES)


def test_html_to_text():
    assert collector.html_to_text('<ul><li><b>A</b> &amp; B</li></ul>') == 'A & B'


# --- fetcher ---

def test_extract_original_url_from_real_topic_page():
    url = fetcher.extract_original_url(TOPIC, 'https://news.hada.io/topic?id=34262')
    assert url == 'https://www.dawo.community/en/'


def test_extract_original_url_ignores_self_links_and_non_http():
    ask_gn = "<a href='topic?id=5' class='bold ud topic-title-link'><h1>Ask GN</h1></a>"
    assert fetcher.extract_original_url(ask_gn, 'https://news.hada.io/topic?id=5') is None
    js = "<a href='javascript:alert(1)' class='topic-title-link'>x</a>"
    assert fetcher.extract_original_url(js, 'https://news.hada.io/topic?id=5') is None
    assert fetcher.extract_original_url('<p>no link</p>', 'https://news.hada.io/') is None


def _article_page(paragraphs: int) -> str:
    body = ''.join(f'<p>이것은 원문 기사의 {i}번째 문단으로 충분히 긴 설명을 담고 있습니다. 내용이 이어집니다.</p>'
                   for i in range(paragraphs))
    return f'<html><head><title>t</title></head><body><nav>메뉴</nav><article>{body}</article></body></html>'


def test_fetch_article_success():
    pages = {
        'https://news.hada.io/topic?id=1000': "<a href='https://example.com/a' class='topic-title-link'>t</a>",
        'https://example.com/a': _article_page(30),
    }
    article = fetcher.fetch_article(pages.__getitem__, make_candidate(0))
    assert article is not None
    assert article.source_url == 'https://example.com/a'
    assert len(article.text) >= fetcher.MIN_ARTICLE_CHARS


def test_fetch_article_direct_source_reads_link_itself():
    candidate = collector.parse_feed(NEWS_FEED, NOW, source=NEWS)[0]
    article = fetcher.fetch_article({candidate.link: _article_page(30)}.__getitem__, candidate)
    assert article is not None
    assert article.source_url == candidate.link


def test_fetch_article_too_short_returns_none():
    pages = {
        'https://news.hada.io/topic?id=1000': "<a href='https://example.com/a' class='topic-title-link'>t</a>",
        'https://example.com/a': _article_page(1),
    }
    assert fetcher.fetch_article(pages.__getitem__, make_candidate(0)) is None


def test_fetch_article_network_error_returns_none():
    def failing_get(url: str) -> str:
        raise requests.ConnectionError('down')

    assert fetcher.fetch_article(failing_get, make_candidate(0)) is None


def test_parse_feed_reads_dates_without_space_after_comma():
    from datetime import datetime, timezone

    from src import collector
    raw = ('<?xml version="1.0"?><rss version="2.0"><channel><title>t</title><item><title>파킹통장 금리</title>'
           '<link>https://www.fnnews.com/news/1</link><pubDate>Sun,27 Sep 2026 08:45:12 +0900</pubDate></item></channel></rss>')
    source = collector.FeedSource('파이낸셜뉴스', 'https://www.fnnews.com/rss/x.xml', direct=True, limit=5)
    [c] = collector.parse_feed(raw, datetime(2026, 9, 27, 1, 0, tzinfo=timezone.utc), source=source)
    assert c.published == datetime(2026, 9, 26, 23, 45, 12, tzinfo=timezone.utc)
