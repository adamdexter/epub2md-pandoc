"""Unit tests for Reddit post -> Markdown conversion (no network)."""

from html_to_md_converter import (
    convert_reddit_to_markdown,
    is_reddit_url,
    reddit_json_to_markdown,
)


def _sample_post():
    return [
        {
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "title": "Why plain text wins",
                            "author": "alice",
                            "subreddit_name_prefixed": "r/programming",
                            "score": 1234,
                            "num_comments": 2,
                            "selftext": "Here is the **body**.\nSecond line.",
                            "is_self": True,
                            "created_utc": 1700000000,
                        },
                    }
                ]
            }
        },
        {
            "data": {
                "children": [
                    {
                        "kind": "t1",
                        "data": {
                            "author": "bob",
                            "score": 42,
                            "body": "Great point!",
                            "replies": {
                                "data": {
                                    "children": [
                                        {
                                            "kind": "t1",
                                            "data": {
                                                "author": "carol",
                                                "score": 5,
                                                "body": "Agreed.",
                                                "replies": "",
                                            },
                                        }
                                    ]
                                }
                            },
                        },
                    },
                    {
                        "kind": "t1",
                        "data": {"author": "dave", "score": -3, "body": "[deleted]", "replies": ""},
                    },
                ]
            }
        },
    ]


def test_is_reddit_url():
    assert is_reddit_url("https://www.reddit.com/r/x/comments/abc/title/")
    assert is_reddit_url("https://old.reddit.com/r/x/comments/abc/")
    assert is_reddit_url("https://redd.it/abc")
    assert not is_reddit_url("https://example.com/r/x")


def test_reddit_json_to_markdown_post_and_comments():
    content, meta, imgs = reddit_json_to_markdown(_sample_post())

    assert meta == {
        "title": "Why plain text wins",
        "author": "alice",
        "source_name": "Reddit",
        "publication_date": "2023-11-14",
    }
    assert imgs == []
    assert "# Why plain text wins" in content
    assert "Posted in r/programming" in content
    assert "Here is the **body**." in content
    assert "## Comments" in content
    assert "**u/bob** (42 points)" in content
    # Nested reply rendered at greater blockquote depth.
    assert "> > **u/carol** (5 points)" in content
    # Deleted comment is skipped.
    assert "dave" not in content


def test_reddit_image_gallery_extraction():
    data = [
        {
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "title": "Gallery",
                            "author": "alice",
                            "is_self": False,
                            "url": "https://www.reddit.com/gallery/abc",
                            "media_metadata": {
                                "x1": {"s": {"u": "https://i.redd.it/one.jpg?width=1"}},
                                "x2": {"s": {"u": "https://i.redd.it/two.png"}},
                            },
                        },
                    }
                ]
            }
        }
    ]
    _content, _meta, imgs = reddit_json_to_markdown(data)
    assert "https://i.redd.it/one.jpg?width=1" in imgs
    assert "https://i.redd.it/two.png" in imgs


def test_reddit_unparseable_json():
    content, meta, imgs = reddit_json_to_markdown({"unexpected": "shape"})
    assert content is None
    assert meta == {}
    assert imgs == []


def test_convert_reddit_end_to_end(tmp_path, monkeypatch):
    import html_to_md_converter as h

    monkeypatch.setattr(h, "fetch_reddit_json", lambda url: (_sample_post(), None))
    ok, msg, fp = convert_reddit_to_markdown(
        "https://www.reddit.com/r/programming/comments/abc/why_plain_text_wins/",
        str(tmp_path),
        download_images=False,
    )
    assert ok, msg
    text = (tmp_path / fp.split("/")[-1]).read_text()
    # Clean frontmatter + clean filename (no "u/" slash artifact).
    assert 'author: "alice"' in text
    assert 'source_name: "Reddit"' in text
    assert fp.endswith("alice - Why plain text wins - Reddit.md")
    assert "# Why plain text wins" in text


def test_convert_reddit_fetch_error(tmp_path, monkeypatch):
    import html_to_md_converter as h

    monkeypatch.setattr(h, "fetch_reddit_json", lambda url: (None, "HTTP 403: blocked"))
    ok, msg, fp = convert_reddit_to_markdown(
        "https://www.reddit.com/r/x/comments/abc/", str(tmp_path), download_images=False
    )
    assert not ok
    assert fp is None
    assert "403" in msg
