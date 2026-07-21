"""Unit tests for web-article pagination helpers."""

from html_to_md_converter import build_page_url, detect_pagination_param


def test_detect_basic_page_param():
    assert detect_pagination_param("https://x.com/a?page=1") == ("page", 1)
    assert detect_pagination_param("https://x.com/a?page=2&sort=asc") == ("page", 2)


def test_detect_alternate_params():
    assert detect_pagination_param("https://x.com/a?pg=3") == ("pg", 3)
    assert detect_pagination_param("https://x.com/a?paged=5") == ("paged", 5)


def test_detect_none_when_absent_or_non_numeric():
    assert detect_pagination_param("https://x.com/a") is None
    assert detect_pagination_param("https://x.com/a?id=5") is None
    assert detect_pagination_param("https://x.com/a?page=next") is None


def test_page_param_priority():
    # 'page' wins over the more ambiguous 'p'.
    assert detect_pagination_param("https://x.com/a?p=9&page=2") == ("page", 2)


def test_build_preserves_other_params_and_order():
    assert (
        build_page_url("https://x.com/a?page=2&sort=asc", "page", 4)
        == "https://x.com/a?page=4&sort=asc"
    )


def test_build_appends_when_missing():
    assert (
        build_page_url("https://x.com/a?sort=asc", "page", 3)
        == "https://x.com/a?sort=asc&page=3"
    )


def test_detect_then_build_round_trip():
    url = "https://x.com/a?page=2"
    param, start = detect_pagination_param(url)
    # Capturing 3 pages starting at 2 -> pages 2, 3, 4.
    pages = [build_page_url(url, param, start + offset) for offset in range(3)]
    assert pages == [
        "https://x.com/a?page=2",
        "https://x.com/a?page=3",
        "https://x.com/a?page=4",
    ]
