"""Unit tests for spine-aware EPUB reference extraction."""

from epub_text import Chapter, extract_reference_text, reference_summary
from tests.conftest import build_synthetic_epub


def test_extract_synthetic(tmp_path):
    epub = tmp_path / "syn.epub"
    build_synthetic_epub(str(epub))
    chapters = extract_reference_text(str(epub))

    assert len(chapters) == 3
    assert all(isinstance(c, Chapter) for c in chapters)
    assert [c.title for c in chapters] == ["Introduction", "Chapter One", "Chapter Two"]
    assert all(c.char_count > 0 and c.text for c in chapters)
    # Spine order is preserved.
    assert chapters[0].title == "Introduction"


def test_summary(tmp_path):
    epub = tmp_path / "syn.epub"
    build_synthetic_epub(str(epub))
    summary = reference_summary(extract_reference_text(str(epub)))
    assert summary["chapter_count"] == 3
    assert summary["total_chars"] > 0
    assert "Introduction" in summary["titles"]


def test_missing_or_bad_epub_returns_empty(tmp_path):
    assert extract_reference_text(str(tmp_path / "does-not-exist.epub")) == []
    bad = tmp_path / "bad.epub"
    bad.write_text("not a zip")
    assert extract_reference_text(str(bad)) == []


def test_per_chapter_truncation(tmp_path):
    epub = tmp_path / "syn.epub"
    build_synthetic_epub(str(epub))
    chapters = extract_reference_text(str(epub), max_chars_per_chapter=20)
    assert all(c.char_count <= 20 + len(" …[truncated]") for c in chapters)
