from datetime import datetime
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

import auto_blogger as ab


def make_http_error(status):
    resp = MagicMock()
    resp.status = status
    resp.reason = "error"
    return HttpError(resp, b"{}")


# --- load_config ---

def test_load_config_reads_env():
    config = ab.load_config({"BLOG_ID": "6191091407195774034", "PUBLISH_HOUR": "21"})
    assert config.blog_id == "6191091407195774034"
    assert config.publish_hour == 21
    assert config.token_file == "token.json"


def test_load_config_defaults_publish_hour_to_20():
    assert ab.load_config({"BLOG_ID": "123"}).publish_hour == 20


@pytest.mark.parametrize("env", [{}, {"BLOG_ID": ""}, {"BLOG_ID": "YOUR_BLOG_ID_HERE"}])
def test_load_config_rejects_missing_or_invalid_blog_id(env):
    with pytest.raises(ab.ConfigError):
        ab.load_config(env)


@pytest.mark.parametrize("hour", ["24", "-1", "eight"])
def test_load_config_rejects_invalid_publish_hour(hour):
    with pytest.raises(ab.ConfigError):
        ab.load_config({"BLOG_ID": "123", "PUBLISH_HOUR": hour})


def test_load_config_server_mode_requires_all_three_values():
    with pytest.raises(ab.ConfigError):
        ab.load_config({"BLOG_ID": "123", "GOOGLE_REFRESH_TOKEN": "rt"})


def test_load_config_server_mode_detected():
    config = ab.load_config({
        "BLOG_ID": "123",
        "GOOGLE_CLIENT_ID": "id",
        "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_REFRESH_TOKEN": "rt",
    })
    assert config.is_server_mode


# --- next_publish_time ---

def test_next_publish_time_same_day_before_hour():
    now = datetime(2026, 9, 25, 15, 0, tzinfo=ab.KST)
    assert ab.next_publish_time(now, 20) == datetime(2026, 9, 25, 20, 0, tzinfo=ab.KST)


def test_next_publish_time_rolls_to_next_day_after_hour():
    now = datetime(2026, 9, 25, 20, 0, tzinfo=ab.KST)
    assert ab.next_publish_time(now, 20) == datetime(2026, 9, 26, 20, 0, tzinfo=ab.KST)


def test_next_publish_time_converts_utc_input_to_kst():
    now = datetime(2026, 9, 25, 6, 0, tzinfo=ab.UTC)  # 15:00 KST
    assert ab.next_publish_time(now, 20) == datetime(2026, 9, 25, 20, 0, tzinfo=ab.KST)


def test_next_publish_time_rejects_naive_datetime():
    with pytest.raises(ValueError):
        ab.next_publish_time(datetime(2026, 9, 25, 15, 0), 20)  # noqa: DTZ001 (의도적으로 시간대 없음)


# --- build_post_body ---

def test_build_post_body():
    body = ab.build_post_body("제목", "<p>본문</p>", ["AI활용"])
    assert body == {
        "kind": "blogger#post",
        "title": "제목",
        "content": "<p>본문</p>",
        "labels": ["AI활용"],
    }


def test_build_post_body_default_labels_are_not_shared():
    first = ab.build_post_body("a", "<p>b</p>")
    first["labels"].append("x")
    assert ab.build_post_body("a", "<p>b</p>")["labels"] == []


@pytest.mark.parametrize("title, content", [("", "<p>b</p>"), ("   ", "<p>b</p>"), ("a", "")])
def test_build_post_body_rejects_empty_fields(title, content):
    with pytest.raises(ValueError):
        ab.build_post_body(title, content)


# --- execute_with_retry ---

def test_execute_with_retry_retries_transient_errors():
    request = MagicMock()
    request.execute.side_effect = [make_http_error(503), {"id": "1"}]
    sleeps = []
    assert ab.execute_with_retry(request, sleep=sleeps.append) == {"id": "1"}
    assert sleeps == [2]


def test_execute_with_retry_does_not_retry_client_errors():
    request = MagicMock()
    request.execute.side_effect = make_http_error(403)
    with pytest.raises(HttpError):
        ab.execute_with_retry(request, sleep=lambda _: None)
    assert request.execute.call_count == 1


def test_execute_with_retry_gives_up_after_max_attempts():
    request = MagicMock()
    request.execute.side_effect = make_http_error(500)
    with pytest.raises(HttpError):
        ab.execute_with_retry(request, max_attempts=3, sleep=lambda _: None)
    assert request.execute.call_count == 3


# --- Blogger calls ---

def test_create_draft_inserts_as_draft():
    service = MagicMock()
    service.posts().insert().execute.return_value = {"id": "42"}
    body = ab.build_post_body("t", "<p>c</p>")

    assert ab.create_draft(service, "123", body) == {"id": "42"}
    service.posts().insert.assert_called_with(blogId="123", body=body, isDraft=True)


def test_publish_post_with_schedule_sends_iso_kst_date():
    service = MagicMock()
    service.posts().publish().execute.return_value = {"status": "SCHEDULED"}
    publish_at = datetime(2026, 9, 25, 20, 0, tzinfo=ab.KST)

    ab.publish_post(service, "123", "42", publish_at)
    service.posts().publish.assert_called_with(
        blogId="123", postId="42", publishDate="2026-09-25T20:00:00+09:00"
    )


def test_publish_post_without_schedule_publishes_now():
    service = MagicMock()
    ab.publish_post(service, "123", "42")
    service.posts().publish.assert_called_with(blogId="123", postId="42")


# --- post_to_blogger ---

CONFIG = ab.load_config({"BLOG_ID": "123", "PUBLISH_HOUR": "20"})


def _service_with_draft():
    service = MagicMock()
    service.posts().insert().execute.return_value = {"id": "42"}
    return service


def test_post_to_blogger_draft_mode_does_not_publish():
    service = _service_with_draft()
    service.posts().publish.reset_mock()
    assert ab.post_to_blogger(service, CONFIG, "t", "<p>c</p>", mode="draft") == {"id": "42"}
    service.posts().publish.assert_not_called()


def test_post_to_blogger_schedule_mode_uses_next_publish_hour():
    service = _service_with_draft()
    now = datetime(2026, 9, 25, 6, 0, tzinfo=ab.UTC)  # 15:00 KST
    ab.post_to_blogger(service, CONFIG, "t", "<p>c</p>", mode="schedule", now=now)
    service.posts().publish.assert_called_with(
        blogId="123", postId="42", publishDate="2026-09-25T20:00:00+09:00"
    )


def test_post_to_blogger_now_mode_publishes_immediately():
    service = _service_with_draft()
    ab.post_to_blogger(service, CONFIG, "t", "<p>c</p>", mode="now")
    service.posts().publish.assert_called_with(blogId="123", postId="42")


def test_post_to_blogger_rejects_unknown_mode():
    with pytest.raises(ValueError):
        ab.post_to_blogger(_service_with_draft(), CONFIG, "t", "<p>c</p>", mode="later")


# --- publish_existing / edit_url ---

def test_publish_existing_schedules_without_creating_new_post():
    service = MagicMock()
    service.posts().insert.reset_mock()
    now = datetime(2026, 9, 25, 12, 13, tzinfo=ab.UTC)  # 21:13 KST -> 다음 날 20시
    ab.publish_existing(service, CONFIG, "42", mode="schedule", now=now)
    service.posts().insert.assert_not_called()
    service.posts().publish.assert_called_with(
        blogId="123", postId="42", publishDate="2026-09-26T20:00:00+09:00"
    )


def test_publish_existing_rejects_draft_mode():
    with pytest.raises(ValueError):
        ab.publish_existing(MagicMock(), CONFIG, "42", mode="draft")


def test_edit_url():
    assert ab.edit_url("123", "42") == "https://www.blogger.com/blog/post/edit/123/42"


# --- 분 단위 예약 (--at HH:MM) ---

def test_next_publish_time_supports_minutes():
    now = datetime(2026, 9, 25, 21, 15, tzinfo=ab.KST)
    assert ab.next_publish_time(now, 21, 20) == datetime(2026, 9, 25, 21, 20, tzinfo=ab.KST)


def test_next_publish_time_with_minutes_rolls_over():
    now = datetime(2026, 9, 25, 21, 25, tzinfo=ab.KST)
    assert ab.next_publish_time(now, 21, 20) == datetime(2026, 9, 26, 21, 20, tzinfo=ab.KST)


def test_parse_hhmm():
    assert ab.parse_hhmm("21:20") == (21, 20)
    assert ab.parse_hhmm("9:05") == (9, 5)


@pytest.mark.parametrize("value", ["24:00", "21:60", "2120", "ab:cd", ""])
def test_parse_hhmm_rejects_invalid(value):
    with pytest.raises(ValueError):
        ab.parse_hhmm(value)


def test_publish_existing_uses_explicit_time():
    service = MagicMock()
    now = datetime(2026, 9, 25, 21, 15, tzinfo=ab.KST)
    ab.publish_existing(service, CONFIG, "42", mode="schedule", now=now, at=(21, 20))
    service.posts().publish.assert_called_with(
        blogId="123", postId="42", publishDate="2026-09-25T21:20:00+09:00"
    )


# --- 영문 글 주소 (슬러그) ---

@pytest.mark.parametrize("slug, expected", [
    ("mortgage-refinance-rates", "mortgage refinance rates"),
    ("  Highway Toll-Refund 2026 ", "highway toll refund 2026"),
    ("a-b-c-d-e-f-g-h", "a b c d e f"),              # 6단어까지만
    ("모두의카드-환급", None),                          # 영어가 아니면 쓰지 않음
    ("refund", None),                                 # 한 단어는 너무 짧음
    ("", None),
])
def test_slug_title(slug, expected):
    assert ab.slug_title(slug) == expected


def test_rename_post_patches_only_title():
    service = MagicMock()
    service.posts().patch().execute.return_value = {"id": "42", "title": "한글 제목"}
    assert ab.rename_post(service, "123", "42", "한글 제목")["title"] == "한글 제목"
    service.posts().patch.assert_called_with(blogId="123", postId="42", body={"title": "한글 제목"})
