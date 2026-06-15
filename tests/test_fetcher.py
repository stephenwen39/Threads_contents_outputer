"""fetcher 純函式測試（不需網路）。"""

from __future__ import annotations

import json

import pytest

from threads_outputer.fetcher import (
    FetchError,
    _extract_posts,
    _iter_embedded_json,
    normalize_username,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("zuck", "zuck"),
        ("@zuck", "zuck"),
        ("  @zuck  ", "zuck"),
        ("https://www.threads.com/@natgeo", "natgeo"),
        ("https://www.threads.net/@foo/post/ABC123", "foo"),
        ("threads.com/@bar?x=1", "bar"),
    ],
)
def test_normalize_username(raw, expected):
    assert normalize_username(raw) == expected


@pytest.mark.parametrize("bad", ["", "   ", "@", "/"])
def test_normalize_username_invalid(bad):
    with pytest.raises(FetchError):
        normalize_username(bad)


def _build_html(dataset: dict) -> str:
    return (
        "<html><head>"
        '<script type="application/json" data-sjs>'
        + json.dumps(dataset)
        + "</script>"
        "</head><body></body></html>"
    )


def _sample_dataset() -> dict:
    return {
        "data": {
            "items": [
                {
                    "pk": "111",
                    "code": "AAA",
                    "taken_at": 1700000000,
                    "like_count": 5,
                    "caption": {"text": "我自己的貼文"},
                    "user": {"username": "target", "pk": "1"},
                    "text_post_app_info": {"direct_reply_count": 2},
                },
                {
                    "pk": "222",
                    "code": "BBB",
                    "taken_at": 1700000100,
                    "like_count": 9,
                    "caption": {"text": "這是別人的內容（轉貼）"},
                    "user": {"username": "someone_else", "pk": "2"},
                },
            ]
        }
    }


def test_iter_embedded_json_parses_script_blocks():
    html = _build_html(_sample_dataset())
    datasets = list(_iter_embedded_json(html))
    assert len(datasets) == 1
    assert datasets[0]["data"]["items"][0]["pk"] == "111"


def test_extract_posts_filters_foreign_authors():
    datasets = [_sample_dataset()]
    posts = _extract_posts(datasets, "target")
    assert len(posts) == 1
    p = posts[0]
    assert p.id == "111"
    assert p.author == "target"
    assert p.text == "我自己的貼文"
    assert p.reply_count == 2
    assert p.url == "https://www.threads.com/@target/post/AAA"


def test_extract_posts_case_insensitive_author():
    datasets = [_sample_dataset()]
    posts = _extract_posts(datasets, "TARGET")
    assert {p.id for p in posts} == {"111"}


def test_extract_posts_keeps_when_author_unknown():
    dataset = {
        "node": {
            "pk": "333",
            "code": "CCC",
            "taken_at": 1700000200,
            "caption": {"text": "沒有作者資訊的貼文"},
        }
    }
    posts = _extract_posts([dataset], "target")
    assert len(posts) == 1
    assert posts[0].author is None
