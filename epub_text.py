#!/usr/bin/env python3
"""Spine-aware plain-text extraction from EPUBs, for the self-improvement judge.

An EPUB is a ZIP of XHTML. To let an LLM judge compare the *original* book to the
produced Markdown, we walk the OPF spine (reading order) and pull clean plain text
per chapter. This mirrors the zipfile/ElementTree walk in
``epub_to_md_converter.extract_epub_metadata`` and adds no new dependencies
(BeautifulSoup is already a core dep; a regex fallback covers its absence).
"""

from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

try:
    from bs4 import BeautifulSoup
    _BS4 = True
except Exception:  # pragma: no cover - graceful degradation, per project convention
    _BS4 = False

_CONTAINER = "META-INF/container.xml"


@dataclass
class Chapter:
    """One spine item's plain text."""

    idref: str
    href: str
    title: str | None
    text: str
    char_count: int


def _local(tag: str) -> str:
    """Strip an XML namespace from a tag (``{ns}item`` -> ``item``)."""
    return tag.split("}")[-1]


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_html(html: str) -> str:
    if _BS4:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text(" ")
    # Regex fallback if BeautifulSoup isn't importable.
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", text)


def _find_opf(zf: zipfile.ZipFile) -> str | None:
    try:
        root = ET.fromstring(zf.read(_CONTAINER))
    except (KeyError, ET.ParseError):
        return None
    for el in root.iter():
        if _local(el.tag) == "rootfile":
            return el.get("full-path")
    return None


def _parse_opf(zf: zipfile.ZipFile, opf_path: str):
    """Return (manifest {id: (href, media_type)}, spine [idref, ...])."""
    manifest: dict[str, tuple] = {}
    spine: list = []
    try:
        root = ET.fromstring(zf.read(opf_path))
    except (KeyError, ET.ParseError):
        return manifest, spine
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "item":
            iid, href = el.get("id"), el.get("href")
            if iid and href:
                manifest[iid] = (href, el.get("media-type", ""))
        elif tag == "itemref":
            idref = el.get("idref")
            if idref:
                spine.append(idref)
    return manifest, spine


def _extract_title(html: str) -> str | None:
    for pattern in (r"<h1[^>]*>(.*?)</h1>", r"<h2[^>]*>(.*?)</h2>", r"<title[^>]*>(.*?)</title>"):
        m = re.search(pattern, html, re.S | re.I)
        if m:
            title = _normalize_ws(_strip_html(m.group(1)))
            if title:
                return title[:120]
    return None


def extract_reference_text(
    epub_path: str,
    max_chars_per_chapter: int = 24_000,
    max_total_chars: int = 600_000,
) -> list:
    """Return spine-ordered :class:`Chapter` plain text for the judge.

    Truncates per-chapter and in total to bound token cost; never raises on a
    malformed EPUB (returns whatever it could read).
    """
    chapters: list = []
    total = 0
    try:
        zf = zipfile.ZipFile(epub_path)
    except (zipfile.BadZipFile, FileNotFoundError):
        return chapters

    with zf:
        opf_path = _find_opf(zf)
        if not opf_path:
            return chapters
        opf_dir = posixpath.dirname(opf_path)
        manifest, spine = _parse_opf(zf, opf_path)

        for idref in spine:
            item = manifest.get(idref)
            if not item:
                continue
            href, media_type = item
            if "html" not in media_type.lower():
                continue
            full = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href
            try:
                raw = zf.read(full).decode("utf-8", errors="replace")
            except KeyError:
                continue

            text = _normalize_ws(_strip_html(raw))
            if not text:
                continue
            if len(text) > max_chars_per_chapter:
                text = text[:max_chars_per_chapter] + " …[truncated]"

            chapters.append(
                Chapter(idref=idref, href=href, title=_extract_title(raw),
                        text=text, char_count=len(text))
            )
            total += len(text)
            if total >= max_total_chars:
                break

    return chapters


def reference_summary(chapters: list) -> dict:
    """Compact stats about the extracted reference text."""
    return {
        "chapter_count": len(chapters),
        "total_chars": sum(c.char_count for c in chapters),
        "titles": [c.title for c in chapters if c.title][:50],
    }
