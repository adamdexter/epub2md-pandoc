"""Tests for rag_distill.py — the RAG/LLM Knowledge Optimized companion pipeline.

CI-safe by construction: a module-scoped socket guard proves zero network, the
Gemini client is injected via the `client_factory` seam (mirroring
test_self_improve.py's mocked-client pattern), and HOME-adjacent state
(key file, usage ledger) is redirected into tmp_path. The google-genai SDK is
never required nor imported.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import rag_distill as rd

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINES_PATH = Path(__file__).resolve().parent / "baselines.json"

SENTINEL_KEY = "AIzaSENTINEL-SECRET-KEY-123"


# --------------------------------------------------------------------------- #
# Zero-network guard (module-scoped: every test in this file runs under it)
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True, scope="module")
def _no_network():
    # Guard connect (not the class itself): imports that subclass socket.socket
    # (e.g. PySocks via requests) keep working, but no connection can ever open.
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guard(self, *args, **kwargs):
        raise AssertionError("network access attempted during rag_distill tests")

    socket.socket.connect = guard
    socket.socket.connect_ex = guard
    try:
        yield
    finally:
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex


# --------------------------------------------------------------------------- #
# Fixtures: fake home, fake client, sample sources
# --------------------------------------------------------------------------- #

@pytest.fixture
def distill_env(tmp_path, monkeypatch):
    """HOME-adjacent state redirected to tmp; SDK 'available'; env key set; no backoff sleeps."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(rd, "KEY_FILE", tmp_path / ".epub2md_gemini_key")
    monkeypatch.setattr(rd, "USAGE_LEDGER", tmp_path / ".epub2md_gemini_usage.json")
    monkeypatch.setenv("GEMINI_API_KEY", SENTINEL_KEY)
    monkeypatch.setattr(rd, "RAG_SUPPORT_AVAILABLE", True)
    monkeypatch.setattr(rd, "RETRY_BACKOFF_S", (0, 0, 0))
    return tmp_path


SOURCE_MD = '''---
title: "The Wealth of Ideas"
author: Maria Santos
year: 2011
---

# Introduction

Maria Santos introduces the argument. The economy grew by 4.5% in 1998, a rate
not seen since the postwar boom.

# Chapter 1: Markets

Markets coordinate activity. Trade volume reached 1,234.5 million units.

| Year | Output |
|------|--------|
| 1998 | 4.5%   |
| 1999 | 88,200 |

More prose after the table with the number 777 in it.

# Chapter 2: Institutions

Institutions shape incentives across 14 countries according to the study.
'''

DIGEST = {
    "summary": "Maria Santos argues that markets coordinate economic activity across many countries.",
    "keywords": ["markets", "coordination"],
    "claims": ["Maria Santos argues markets coordinate economic activity."],
    "facts_numeric": ["The economy grew by 4.5% in 1998."],
    "terms": [{"term": "Market", "definition": "A mechanism where buyers and sellers coordinate exchange."}],
    "qa": [{"q": "What do markets do according to Maria Santos",
            "a": "They coordinate economic activity among participants."}],
    "entities": [{"name": "Maria Santos", "kind": "person", "one_liner": "Economist and author."}],
}

REDUCE = {
    "executive_summary": "The Wealth of Ideas by Maria Santos explains how markets and institutions shape growth.",
    "thesis": ["Markets coordinate economic activity.", "Institutions shape incentives."],
    "themes": [{"theme": "market coordination",
                "synthesis": "Across chapters, Maria Santos shows markets coordinating trade.",
                "keywords": ["markets"]}],
    "glossary": [{"term": "Market", "definition": "A mechanism where buyers and sellers coordinate exchange."}],
    "question_bank": [{"q": "What is the core thesis of The Wealth of Ideas",
                       "a": "Markets coordinate activity while institutions shape incentives."}],
    "entity_index": [{"name": "Maria Santos", "kind": "person", "one_liner": "Economist and author."}],
}


class FakeResponse:
    def __init__(self, text, prompt=1000, cand=200, thoughts=10):
        self.text = text
        self.usage_metadata = SimpleNamespace(prompt_token_count=prompt,
                                              candidates_token_count=cand,
                                              thoughts_token_count=thoughts)


class FakeClient:
    """genai.Client-shaped: client.models.generate_content(model=, contents=, config=)."""

    def __init__(self, handler):
        self.calls = []
        outer = self

        class _Models:
            def generate_content(self, *, model, contents, config=None):
                outer.calls.append({"model": model, "contents": contents, "config": config})
                return handler(model, contents, config, len(outer.calls))

        self.models = _Models()


def make_handler(digest=None, reduce_out=None, prompt_tokens=1000):
    digest = digest if digest is not None else DIGEST
    reduce_out = reduce_out if reduce_out is not None else REDUCE

    def handler(model, contents, config, call_no):
        if "You are extracting knowledge" in contents:
            return FakeResponse(json.dumps(digest), prompt=prompt_tokens)
        return FakeResponse(json.dumps(reduce_out), prompt=prompt_tokens)

    return handler


def write_md(tmp_path, text=SOURCE_MD, name="The Wealth of Ideas - Maria Santos 2011.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def run_distill(md_path, handler=None, logs=None, **kwargs):
    client = FakeClient(handler or make_handler())
    result = rd.distill_markdown(str(md_path), client_factory=lambda: client,
                                 log=(logs.append if logs is not None else (lambda m: None)),
                                 **kwargs)
    return result, client


# --------------------------------------------------------------------------- #
# 1. Import without the SDK
# --------------------------------------------------------------------------- #

def test_import_without_sdk(tmp_path):
    real_find_spec = importlib.util.find_spec
    importlib.util.find_spec = (
        lambda name, *a, **k: None if name == "google.genai" else real_find_spec(name, *a, **k))
    try:
        importlib.reload(rd)
        assert rd.RAG_SUPPORT_AVAILABLE is False
        assert rd.is_available() is False
        md = tmp_path / "b.md"
        md.write_text("# Title\n\nSome body text.\n", encoding="utf-8")
        result = rd.distill_markdown(str(md))
        assert result.ok is False
        assert result.skipped_reason == "sdk_missing"
        assert "google.genai" not in sys.modules  # no SDK import was ever attempted
    finally:
        importlib.util.find_spec = real_find_spec
        importlib.reload(rd)


# --------------------------------------------------------------------------- #
# 2. Key resolution
# --------------------------------------------------------------------------- #

def test_key_env_wins(distill_env):
    rd.KEY_FILE.write_text("file-key-should-lose\n", encoding="utf-8")
    os.chmod(rd.KEY_FILE, 0o600)
    assert rd.resolve_api_key(log=lambda m: None) == SENTINEL_KEY  # env beats file


def test_key_file_fallback(distill_env, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    rd.KEY_FILE.write_text("file-key-abc\n", encoding="utf-8")
    os.chmod(rd.KEY_FILE, 0o600)
    logs = []
    assert rd.resolve_api_key(log=logs.append) == "file-key-abc"
    assert not any("chmod" in line for line in logs)
    os.chmod(rd.KEY_FILE, 0o644)                       # loose perms: warn but proceed
    assert rd.resolve_api_key(log=logs.append) == "file-key-abc"
    assert any("not chmod 600" in line for line in logs)


def test_no_key_skips(distill_env, monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    md = write_md(tmp_path)
    constructed = []
    result = rd.distill_markdown(str(md), client_factory=lambda: constructed.append(1),
                                 log=lambda m: None)
    assert result.ok is False and result.skipped_reason == "no_api_key"
    assert constructed == []                           # zero client constructions


# --------------------------------------------------------------------------- #
# 3. Key scrubbing on every outbound string
# --------------------------------------------------------------------------- #

def test_key_never_in_output(distill_env, tmp_path):
    md = write_md(tmp_path)

    def exploding(model, contents, config, call_no):
        raise RuntimeError(f"auth rejected for key {SENTINEL_KEY} at endpoint")

    logs, status = [], {}
    result, _ = run_distill(md, handler=exploding, logs=logs, status=status)
    assert result.ok is False
    blob = "\n".join(logs) + json.dumps(status) + str(result.error) + str(result.skipped_reason)
    assert SENTINEL_KEY not in blob
    assert "•••" in "\n".join(logs)                    # scrub marker in the retry logs


# --------------------------------------------------------------------------- #
# 4-8. Chunk planning
# --------------------------------------------------------------------------- #

def test_plan_chunks_heading_split():
    parts = []
    for p in range(3):
        parts.append(f"# Part {p + 1}")
        for c in range(3):
            parts.append(f"## Chapter {p * 3 + c + 1}")
            parts.append(("chapter body words here " * 60).strip())
    md = "\n\n".join(parts)
    chunks = rd.plan_chunks(md, target_tokens=400, max_tokens=800, min_tokens=10)
    assert len(chunks) == 9
    assert [c.heading_path for c in chunks] == [
        [f"Part {p + 1}", f"Chapter {p * 3 + c + 1}"] for p in range(3) for c in range(3)]
    assert [c.index for c in chunks] == list(range(9))          # document order
    assert all("chapter body words" in c.text for c in chunks)


def test_plan_chunks_oversize_descends_and_part_splits():
    sub = ("sub section words " * 90).strip()                    # ~400 tok each
    long_paras = "\n\n".join(("long paragraph words " * 20).strip() for _ in range(10))
    md = f"# Big\n\n## Sub A\n\n{sub}\n\n## Sub B\n\n{sub}\n\n# Long\n\n{long_paras}"
    chunks = rd.plan_chunks(md, target_tokens=300, max_tokens=500, min_tokens=10)
    paths = [tuple(c.heading_path) for c in chunks]
    assert ("Big", "Sub A") in paths and ("Big", "Sub B") in paths   # descended to children
    part_chunks = [c for c in chunks if "(part " in c.heading_path[-1]]
    assert len(part_chunks) >= 2                                 # oversized leaf split at \n\n
    assert all(c.heading_path[-1].startswith("Long (part ") for c in part_chunks)
    assert all(c.token_estimate <= 500 for c in part_chunks)


def test_plan_chunks_merges_tiny_and_caps():
    md = "# A\n\ntiny a text.\n\n# B\n\ntiny b text.\n\n# C\n\n" + ("c section words " * 100).strip()
    chunks = rd.plan_chunks(md, target_tokens=300, max_tokens=800, min_tokens=100)
    assert len(chunks) == 2
    assert chunks[0].heading_path == ["A + B"]                   # sub-min siblings merged
    assert "tiny a text." in chunks[0].text and "tiny b text." in chunks[0].text

    md2 = "\n\n".join(f"# S{i}\n\nsection words " + ("w " * 40).strip() for i in range(60))
    chunks2 = rd.plan_chunks(md2, target_tokens=50, max_tokens=100, min_tokens=1, max_chunks=10)
    assert 0 < len(chunks2) <= 10                                # repacked under the cap
    assert [c.index for c in chunks2] == list(range(len(chunks2)))
    joined = "\n".join(c.text for c in chunks2)
    assert "# S59" in joined or "section words" in joined        # no content lost


HAZARD_MD = """# Alpha

Intro text for alpha.

---

More text after the page break rule.

```
# not a heading
| not | a | table |
```

# Table of Contents

- toc junk line

# Pages

pages junk line

# Guide

guide junk line

# Landmarks

landmarks junk line

# Beta

""" + ("beta filler words " * 40).strip() + """

<table>
<tr><td>55</td></tr>
</table>

#### Orphan

Deep orphan text sits under a non-monotonic heading level.

## Figures

Figure 9: appendix figure with 999 units.
"""


def test_plan_chunks_hazards():
    chunks = rd.plan_chunks(HAZARD_MD, target_tokens=80, max_tokens=1000, min_tokens=1)
    all_text = "\n".join(c.text for c in chunks)
    all_titles = [t for c in chunks for t in c.heading_path]
    # Dropped navigation sections and the synthetic Figures section never reach the LLM.
    for banned in ("Table of Contents", "Pages", "Guide", "Landmarks", "Figures"):
        assert banned not in all_titles
    for junk in ("toc junk line", "pages junk line", "guide junk line",
                 "landmarks junk line", "999"):
        assert junk not in all_text
    # Mid-document --- is a page-break rule, not a heading; its neighbors survive.
    assert "More text after the page break rule." in all_text
    assert not any(t.strip() == "---" for t in all_titles)
    # Headings inside code fences are not headings; the fence is never split.
    assert "# not a heading" in all_text
    assert "not a heading" not in " ".join(all_titles)
    # HTML tables are atomic within a single chunk.
    table_chunks = [c for c in chunks if "<table>" in c.text]
    assert len(table_chunks) == 1 and "</table>" in table_chunks[0].text
    # Orphan #### attaches under Beta with a clamped, sane path.
    orphan = [c for c in chunks if c.heading_path[-1] == "Orphan"]
    assert orphan and orphan[0].heading_path[0] == "Beta"
    # The Figures section is routed to VerbatimAssets instead.
    assets = rd.extract_verbatim_assets(HAZARD_MD)
    assert any(t["kind"] == "figure" and "999" in t["raw"] for t in assets.tables)


def test_headingless_fallback_with_overlap():
    paras = [f"Paragraph {name} " + ("filler words here " * 15).strip() for name in "ABCDEFGHIJKL"]
    pages = ["\n\n".join(paras[i:i + 3]) for i in range(0, 12, 3)]
    md = "\n\n---\n\n".join(pages)                     # --- as PDF page-break markers
    chunks = rd.plan_chunks(md, target_tokens=150)
    assert len(chunks) >= 3
    assert all(re.fullmatch(r"Pages ~\d+–\d+", c.heading_path[0]) for c in chunks)
    for a, b in zip(chunks, chunks[1:]):               # one-paragraph overlap between windows
        assert a.text.split("\n\n")[-1] == b.text.split("\n\n")[0]
    # Without markers the fallback still packs, with generic section paths.
    chunks2 = rd.plan_chunks("\n\n".join(paras), target_tokens=150)
    assert all(c.heading_path[0].startswith("Section ") for c in chunks2)


# --------------------------------------------------------------------------- #
# 9. Verbatim asset extraction + numeral normalization
# --------------------------------------------------------------------------- #

TABLES_MD = """# Data Chapter

Prose before the assets.

| A | B |
|---|---|
| 1,234.5 | 90% |

<table>
<tr><td>−42</td></tr>
</table>

<figure>
<img src="x.png"/>
<figcaption>Figure 3: growth of 88,200 units</figcaption>
</figure>
"""


def test_extract_tables_and_numerals():
    assets = rd.extract_verbatim_assets(TABLES_MD)
    kinds = [t["kind"] for t in assets.tables]
    assert kinds == ["pipe", "html", "figure"]
    pipe, html, fig = assets.tables
    assert pipe["raw"] == "| A | B |\n|---|---|\n| 1,234.5 | 90% |"      # byte-identical
    assert html["raw"] == "<table>\n<tr><td>−42</td></tr>\n</table>"
    assert fig["raw"].startswith("<figure>") and fig["raw"].endswith("</figure>")
    assert all(t["heading_path"] == ["Data Chapter"] for t in assets.tables)
    assert {"1234.5", "90", "-42", "88200"} <= assets.numerals_tables
    # Normalization: commas stripped, % dropped, unicode minus unified, sentence dot trimmed.
    assert rd.extract_numerals("growth of 1,234.5% and −5 units, then 42.") == {"1234.5", "-5", "42"}


# --------------------------------------------------------------------------- #
# 10. Accuracy-critical table handling
# --------------------------------------------------------------------------- #

SMALL_TABLE = "| X | Y |\n|---|---|\n| 21 | 33 |"
BIG_TABLE = "| id | value |\n|----|-------|\n" + "\n".join(f"| r{i} | {9000 + i} |" for i in range(45))

CLEAN_DIGEST = {"summary": "Maria Santos discusses tabulated results without repeating them.",
                "keywords": ["tables"], "claims": [], "facts_numeric": [], "terms": [],
                "qa": [], "entities": [{"name": "Maria Santos", "kind": "person", "one_liner": "Economist."}]}


def _tables_source():
    return ('---\ntitle: "Tables Book"\nauthor: Maria Santos\nyear: 2011\n---\n\n'
            f"# Chapter A\n\nProse about the first table.\n\n{SMALL_TABLE}\n\n"
            f"# Chapter B\n\nProse about the second table.\n\n{BIG_TABLE}\n")


def test_accuracy_critical_tables_verbatim(distill_env, tmp_path):
    handler = make_handler(digest=CLEAN_DIGEST)
    ac_dir = tmp_path / "ac"
    ac_dir.mkdir()
    md_ac = write_md(ac_dir, _tables_source(), name="Tables Book - Maria Santos 2011.md")
    result, client = run_distill(md_ac, handler=handler, accuracy_critical=True)
    assert result.ok, result
    companion = Path(result.companion_path).read_text(encoding="utf-8")
    assert SMALL_TABLE in companion and BIG_TABLE in companion   # ALL tables, byte-identical
    prompts = [c["contents"] for c in client.calls if "You are extracting knowledge" in c["contents"]]
    assert prompts and all("[TABLE:" in p for p in prompts)      # excised with placeholder
    assert not any("| r5 |" in p for p in prompts)               # LLM never sees table rows
    v = result.verification
    assert v.table_numerals_present == v.table_numerals_total > 0

    norm_dir = tmp_path / "norm"
    norm_dir.mkdir()
    md_norm = write_md(norm_dir, _tables_source(), name="Tables Book - Maria Santos 2011.md")
    result2, client2 = run_distill(md_norm, handler=handler, accuracy_critical=False)
    assert result2.ok
    companion2 = Path(result2.companion_path).read_text(encoding="utf-8")
    assert SMALL_TABLE in companion2                             # <=40-row table appended
    assert BIG_TABLE not in companion2                           # >40-row table omitted (normal mode)
    prompts2 = [c["contents"] for c in client2.calls if "You are extracting knowledge" in c["contents"]]
    assert any("| r5 |" in p for p in prompts2)                  # normal mode leaves tables in chunks


# --------------------------------------------------------------------------- #
# 11. Numeral firewall
# --------------------------------------------------------------------------- #

def _bad_digest():
    d = dict(CLEAN_DIGEST)
    d["summary"] = "The rate reached 42.7 percent according to the study."
    return d


def test_numeral_firewall(distill_env, tmp_path, monkeypatch):
    # Normal mode: annotate + flag, file still written.
    md = write_md(tmp_path, name="A - Maria Santos 2011.md")
    logs = []
    result, _ = run_distill(md, handler=make_handler(digest=_bad_digest()), logs=logs)
    assert result.ok
    companion = Path(result.companion_path).read_text(encoding="utf-8")
    assert "⚠ unverified figure" in companion
    assert result.verification.flagged_numbers == ["42.7"]
    assert any("unverified" in line for line in logs)

    # Accuracy-critical: the block is dropped; no unverified number survives.
    md2 = write_md(tmp_path, name="B - Maria Santos 2011.md")
    result2, _ = run_distill(md2, handler=make_handler(digest=_bad_digest()), accuracy_critical=True)
    assert result2.ok
    companion2 = Path(result2.companion_path).read_text(encoding="utf-8")
    assert "42.7" not in companion2
    assert result2.verification.dropped_items >= 1

    # Unremovable survivor (drops disabled) => abort, no file.
    md3 = write_md(tmp_path, name="C - Maria Santos 2011.md")
    with monkeypatch.context() as m:
        m.setattr(rd, "_drop_unit", lambda unit, report: None)
        result3, _ = run_distill(md3, handler=make_handler(digest=_bad_digest()),
                                 accuracy_critical=True)
    assert result3.ok is False and result3.skipped_reason == "verification_failed"
    assert not Path(rd._default_out_path(str(md3))).exists()

    # Exemptions: small ints (0-12) and 4-digit years present in the source.
    exempt_digest = dict(CLEAN_DIGEST)
    exempt_digest["summary"] = "In 2011 the field changed across 7 distinct areas."
    md4 = write_md(tmp_path, name="D - Maria Santos 2011.md")
    result4, _ = run_distill(md4, handler=make_handler(digest=exempt_digest))
    assert result4.ok and result4.verification.flagged_numbers == []


# --------------------------------------------------------------------------- #
# 12. Table-numeral survival + repair
# --------------------------------------------------------------------------- #

def test_table_numeral_survival_repair():
    assets = rd.extract_verbatim_assets(SOURCE_MD)
    report, logs = rd.VerificationReport(), []
    doc = "# Companion\n\nProse without any of the table numerals."
    fixed = rd._verify_table_survival(doc, assets.tables, report, logs.append)
    assert "| 1999 | 88,200 |" in fixed                          # table re-appended
    assert any("repair" in line for line in logs)
    assert report.table_numerals_present == report.table_numerals_total > 0

    report2, logs2 = rd.VerificationReport(), []
    same = rd._verify_table_survival(fixed, assets.tables, report2, logs2.append)
    assert same == fixed and logs2 == []                         # clean case: no repair
    assert report2.table_numerals_present == report2.table_numerals_total


# --------------------------------------------------------------------------- #
# 13. Assembly structure (full mocked end-to-end)
# --------------------------------------------------------------------------- #

FILLER = ("Maria Santos writes at length about the coordination of markets and the "
          "slow evolution of institutions across many countries and decades. ")


def _big_source():
    pad = (FILLER * 600).strip()
    return ('---\ntitle: "The Wealth of Ideas"\nauthor: Maria Santos\nyear: 2011\n---\n\n'
            f"# Introduction\n\n{pad}\n\n# Chapter 1: Markets\n\n{pad}\n\n"
            "| Year | Output |\n|------|--------|\n| 1998 | 4.5%   |\n\n"
            f"# Chapter 2: Institutions\n\n{pad}\n")


def test_assembly_structure(distill_env, tmp_path):
    long_digest = dict(DIGEST)
    long_digest["summary"] = ("Maria Santos develops the argument about markets in detail. " * 90).strip()
    md = write_md(tmp_path, _big_source())
    result, _ = run_distill(md, handler=make_handler(digest=long_digest))
    assert result.ok and result.chunks_total == 3
    assert result.companion_path == str(md)[:-3] + ".rag.md"     # <stem>.rag.md beside the .md
    companion = Path(result.companion_path).read_text(encoding="utf-8")

    for key in ('title: "The Wealth of Ideas"', 'author: "Maria Santos"', "year: 2011",
                'document_type: "rag_distillate"',
                'source_document: "The Wealth of Ideas - Maria Santos 2011.md"',
                'source_kind: "epub"', "accuracy_critical: false",
                'model: "gemini-3.5-flash-lite + gemini-3.6-flash"',
                "converter_version:", "generated:"):
        assert key in companion, key

    h2s = ["## What is *The Wealth of Ideas* about?", "## Core Thesis and Argument Map",
           "## Key Facts and Figures", "## Concept Glossary", "## Introduction",
           "## Chapter 1: Markets", "## Chapter 2: Institutions", "## Cross-Chapter Synthesis",
           "## Question Bank", "## Entity Index", "## Verbatim Tables and Figures (source-exact)"]
    positions = [companion.index(h) for h in h2s]                # fixed H2 order
    assert positions == sorted(positions)

    assert companion.count("*[Source: The Wealth of Ideas — Maria Santos (2011)") >= 10
    # 500-word ceiling: the 810-word summary must split into footered blocks <=500 words.
    pieces = rd._split_long_block(long_digest["summary"])
    assert len(pieces) >= 2 and all(len(p.split()) <= 500 for p in pieces)
    intro = companion[companion.index("## Introduction"):companion.index("## Chapter 1: Markets")]
    assert intro.count("*[Source:") >= 2

    # Question-shaped H3s everywhere QA content appears.
    qbank = companion[companion.index("## Question Bank"):companion.index("## Entity Index")]
    q_h3s = [line for line in qbank.split("\n") if line.startswith("### ")]
    assert q_h3s and all(line.endswith("?") for line in q_h3s)
    assert '### What are the key claims of "Introduction"?' in companion

    # Pronoun-led blocks get the subject prefixed.
    assert "Maria Santos: They coordinate economic activity among participants." in companion
    assert "\nThey coordinate economic activity" not in companion

    # Empty sections get a one-line note, never padding.
    empty_reduce = dict(REDUCE, glossary=[], thesis=[])
    empty_digest = dict(DIGEST, terms=[])
    md2 = write_md(tmp_path, name="Small - Maria Santos 2011.md")
    result2, _ = run_distill(md2, handler=make_handler(digest=empty_digest, reduce_out=empty_reduce))
    companion2 = Path(result2.companion_path).read_text(encoding="utf-8")
    gloss = companion2[companion2.index("## Concept Glossary"):]
    assert gloss.split("\n\n")[1] == "_None found in this document._"


# --------------------------------------------------------------------------- #
# 14-15. Source untouched; companions never re-distilled
# --------------------------------------------------------------------------- #

def test_source_md_untouched(distill_env, tmp_path):
    md = write_md(tmp_path)
    before = hashlib.sha256(md.read_bytes()).hexdigest()
    baselines_before = BASELINES_PATH.read_bytes()
    result, _ = run_distill(md)
    assert result.ok
    assert hashlib.sha256(md.read_bytes()).hexdigest() == before   # full .md never modified
    assert BASELINES_PATH.read_bytes() == baselines_before


def test_companion_never_input(distill_env, tmp_path):
    companion = tmp_path / "Book.rag.md"
    companion.write_text("# Existing companion\n", encoding="utf-8")
    result, client = run_distill(companion)
    assert result.ok is False
    assert "companion" in (result.error or "")
    assert client.calls == []
    assert not (tmp_path / "Book.rag.rag.md").exists()


# --------------------------------------------------------------------------- #
# 16. Cost math
# --------------------------------------------------------------------------- #

def test_cost_math(distill_env, tmp_path, monkeypatch):
    usd, is_est = rd.compute_call_cost("gemini-3.6-flash", 1_000_000, 100_000)
    assert is_est is False and usd == pytest.approx(1.50 + 0.75)   # thoughts billed as output

    lo, _ = rd.compute_call_cost("gemini-3.1-pro-preview", 199_000, 1_000)
    hi, _ = rd.compute_call_cost("gemini-3.1-pro-preview", 201_000, 1_000)
    assert lo == pytest.approx(0.410) and hi == pytest.approx(0.822)  # per-call 200k breakpoint

    assert rd.compute_call_cost("gemini-flash-latest", 10, 10) == (None, True)
    assert rd.compute_call_cost("totally-unknown", 10, 10) == (None, True)

    line = rd.format_usage_line(
        rd.UsageTotals(calls=18, input_tokens=412_300, output_tokens=28_100,
                       thought_tokens=900, cost_usd=0.0132),
        {"cost_usd": 0.87, "uncosted_calls": 0})
    assert line == "LLM usage: 18 calls, 412,300 in / 28,100 out tok, $0.0132 this run — lifetime $0.87"
    line2 = rd.format_usage_line(
        rd.UsageTotals(calls=18, input_tokens=412_300, output_tokens=28_100,
                       cost_usd=None, estimate_only=True),
        {"cost_usd": 0.87, "uncosted_calls": 5})
    assert line2 == ("LLM usage: 18 calls, 412,300 in / 28,100 out tok, "
                     "cost unknown (unpriced model) this run — lifetime $0.87 + 5 uncosted calls")

    # Priced end-to-end: 2 calls, each (1000 in, 200+10 out) => hand-calced total.
    md = write_md(tmp_path)
    result, _ = run_distill(md)
    expected = ((1000 * 0.30 + 210 * 2.50) + (1000 * 1.50 + 210 * 7.50)) / 1e6
    assert result.usage.cost_usd == pytest.approx(expected)
    assert result.usage.output_tokens == 420 and result.usage.thought_tokens == 20

    # Unknown model => tokens-only; a dollar figure is never fabricated.
    monkeypatch.setattr(rd, "QUALITY_MODELS",
                        {"standard": ("mystery-map", "mystery-reduce"),
                         "max": ("mystery-map", "mystery-reduce")})
    md2 = write_md(tmp_path, name="U - Maria Santos 2011.md")
    logs = []
    result2, _ = run_distill(md2, logs=logs)
    assert result2.ok
    assert result2.usage.cost_usd is None and result2.usage.estimate_only is True
    assert result2.usage.input_tokens == 2000                     # tokens still reported
    assert any("cost unknown (unpriced model)" in line for line in logs)
    row = json.loads(rd.USAGE_LEDGER.read_text())["runs"][-1]
    assert row["cost_usd"] is None and row["estimate_only"] is True


# --------------------------------------------------------------------------- #
# 17. Cost caps: preflight and mid-run
# --------------------------------------------------------------------------- #

def test_cost_cap_preflight_and_midrun(distill_env, tmp_path):
    big = write_md(tmp_path, "Filler words about markets and things. " * 12_000,
                   name="Huge - Maria Santos 2011.md")
    logs = []
    result, client = run_distill(big, logs=logs, cost_cap_usd=0.001)
    assert result.ok is False and result.skipped_reason == "cost_cap"
    assert client.calls == []                                     # zero API calls
    assert not rd.USAGE_LEDGER.exists()                           # nothing was billed
    assert any("rag_distill_cost_cap_usd" in line for line in logs)

    md = write_md(tmp_path)
    result2, client2 = run_distill(md, handler=make_handler(prompt_tokens=10_000_000),
                                   cost_cap_usd=0.10)
    assert result2.ok is False and result2.skipped_reason == "cost_cap_midrun"
    assert len(client2.calls) == 1                                # aborted after the breach
    assert not Path(rd._default_out_path(str(md))).exists()       # no companion
    assert not list(tmp_path.glob("**/*.tmp"))                    # no orphan tmp
    row = json.loads(rd.USAGE_LEDGER.read_text())["runs"][-1]
    assert row["outcome"] == "aborted" and row["calls"] == 1 and row["cost_usd"] > 0.10


# --------------------------------------------------------------------------- #
# 18. Chunk failure paths + JSON repair
# --------------------------------------------------------------------------- #

def _five_chunks(text, **kwargs):
    return [rd.Chunk(i, [f"Chapter {i + 1}"], f"Chapter {i + 1} body prose about markets.", 400)
            for i in range(5)]


def _failing_handler(fail_on, invalid_on=(), repair_ok=True):
    def handler(model, contents, config, call_no):
        if "You are extracting knowledge" in contents:
            for name in fail_on:
                if name in contents:
                    raise RuntimeError("boom transient")
            for name in invalid_on:
                if name in contents:
                    if "Re-emit valid JSON only" in contents and repair_ok:
                        return FakeResponse(json.dumps(DIGEST))
                    return FakeResponse("NOT { json")
            return FakeResponse(json.dumps(DIGEST))
        return FakeResponse(json.dumps(REDUCE))
    return handler


def test_chunk_failure_paths(distill_env, tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "plan_chunks", _five_chunks)

    # One persistent failure: placeholder digest, run continues, outcome=partial.
    md = write_md(tmp_path, name="P1 - Maria Santos 2011.md")
    logs = []
    result, _ = run_distill(md, handler=_failing_handler(["Chapter 2"]), logs=logs)
    assert result.ok is True and result.chunks_failed == 1 and result.chunks_total == 5
    companion = Path(result.companion_path).read_text(encoding="utf-8")
    assert "could not be distilled" in companion
    assert any("chunk 2/5 failed" in line for line in logs)
    assert json.loads(rd.USAGE_LEDGER.read_text())["runs"][-1]["outcome"] == "partial"

    # 40% failing => abort, no companion, spend still recorded.
    md2 = write_md(tmp_path, name="P2 - Maria Santos 2011.md")
    result2, _ = run_distill(md2, handler=_failing_handler(["Chapter 2", "Chapter 4"]))
    assert result2.ok is False and result2.skipped_reason == "too_many_failures"
    assert not Path(rd._default_out_path(str(md2))).exists()
    assert json.loads(rd.USAGE_LEDGER.read_text())["runs"][-1]["outcome"] == "failed"

    # Accuracy-critical tolerates zero failed chunks.
    md3 = write_md(tmp_path, name="P3 - Maria Santos 2011.md")
    result3, _ = run_distill(md3, handler=_failing_handler(["Chapter 2"]), accuracy_critical=True)
    assert result3.ok is False and result3.skipped_reason == "too_many_failures"
    assert not Path(rd._default_out_path(str(md3))).exists()

    # Bad JSON: exactly one repair reprompt, which succeeds.
    md4 = write_md(tmp_path, name="P4 - Maria Santos 2011.md")
    result4, client4 = run_distill(md4, handler=_failing_handler([], invalid_on=["Chapter 1"]))
    assert result4.ok is True and result4.chunks_failed == 0
    repairs = [c for c in client4.calls if "Your previous output was invalid JSON" in c["contents"]]
    assert len(repairs) == 1

    # Both attempts invalid => placeholder, run continues.
    md5 = write_md(tmp_path, name="P5 - Maria Santos 2011.md")
    result5, _ = run_distill(md5, handler=_failing_handler([], invalid_on=["Chapter 1"],
                                                           repair_ok=False))
    assert result5.ok is True and result5.chunks_failed == 1
    assert "could not be distilled" in Path(result5.companion_path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 19. Ledger roundtrip
# --------------------------------------------------------------------------- #

def test_ledger_roundtrip(distill_env, tmp_path, monkeypatch):
    usage = rd.UsageTotals(calls=2, input_tokens=100, output_tokens=50,
                           thought_tokens=5, cost_usd=0.01)
    kw = dict(source_kind="epub", quality="standard", models=["m1", "m2"],
              accuracy_critical=False, outcome="success", chunks=3, chunks_failed=0)
    life = rd.record_run(usage, file_name="A.md", **kw)
    assert life["calls"] == 2 and life["cost_usd"] == pytest.approx(0.01)
    life = rd.record_run(usage, file_name="B.md", **kw)
    assert life["calls"] == 4 and life["cost_usd"] == pytest.approx(0.02)   # monotonic

    data = json.loads(rd.USAGE_LEDGER.read_text())
    assert data["version"] == 1 and len(data["runs"]) == 2
    assert data["runs"][1]["file"] == "B.md" and data["runs"][1]["outcome"] == "success"
    assert (rd.USAGE_LEDGER.stat().st_mode & 0o777) == 0o600                # chmod 600

    # FIFO cap 100: oldest rows fall off; lifetime totals are independent of the cap.
    data["runs"] = [dict(data["runs"][0], file=f"F{i}.md") for i in range(100)]
    rd.USAGE_LEDGER.write_text(json.dumps(data), encoding="utf-8")
    life = rd.record_run(usage, file_name="LAST.md", **kw)
    data2 = json.loads(rd.USAGE_LEDGER.read_text())
    assert len(data2["runs"]) == 100
    assert data2["runs"][-1]["file"] == "LAST.md" and data2["runs"][0]["file"] == "F1.md"
    assert life["calls"] == 6                                               # kept accumulating

    # Unpriced runs bump uncosted_calls, never fabricate dollars.
    unpriced = rd.UsageTotals(calls=3, input_tokens=10, output_tokens=5,
                              cost_usd=None, estimate_only=True)
    life = rd.record_run(unpriced, file_name="U.md", **kw)
    assert life["uncosted_calls"] == 3 and life["cost_usd"] == pytest.approx(0.03)

    # Atomic: an injected write failure leaves the previous valid JSON and no .tmp.
    before = rd.USAGE_LEDGER.read_text()
    with monkeypatch.context() as m:
        m.setattr(rd.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        life = rd.record_run(usage, file_name="FAIL.md", **kw)              # must not raise
        assert life["calls"] == 11
    assert rd.USAGE_LEDGER.read_text() == before
    assert not list(tmp_path.glob("*.tmp"))

    # Corrupt ledger: rotated to .bak, fresh start, distillation never blocked.
    rd.USAGE_LEDGER.write_text("{ this is not json", encoding="utf-8")
    assert rd.load_usage_ledger() == {}
    assert Path(str(rd.USAGE_LEDGER) + ".bak").exists()


# --------------------------------------------------------------------------- #
# 20. Dry run via main(): zero network, no SDK, exit 0
# --------------------------------------------------------------------------- #

def test_dry_run_zero_network(distill_env, tmp_path, monkeypatch, capsys):
    md = write_md(tmp_path)
    monkeypatch.setattr(sys, "argv", ["rag-distill", str(md), "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        rd.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "chunks" in out and "est." in out and "dry-run" in out
    assert "chunk 1:" in out                                     # plan printed
    assert "google.genai" not in sys.modules                     # SDK never imported
    assert not Path(rd._default_out_path(str(md))).exists()      # nothing written


# --------------------------------------------------------------------------- #
# 21-22. GUI wiring (Flask test client; spec contract for gui.py)
# --------------------------------------------------------------------------- #

@pytest.fixture
def gui_mod(tmp_path, monkeypatch):
    gui = pytest.importorskip("gui")
    if not hasattr(gui, "_run_rag_distill"):
        pytest.skip("integration: requires gui wiring (_run_rag_distill / /rag_distill_status)")
    monkeypatch.setattr(gui, "PREFERENCES_FILE", str(tmp_path / "prefs.json"))
    return gui


def test_gui_prefs_roundtrip(gui_mod):
    client = gui_mod.app.test_client()
    data = client.get("/get_preferences").get_json()
    assert data["rag_distill_enabled"] is False                  # defaults: everything off
    assert data["rag_distill_enabled_pdf"] is False
    assert data["rag_distill_quality"] == "standard"
    assert data["rag_accuracy_critical_epub"] is False

    resp = client.post("/save_preferences", json={
        "rag_distill_enabled": True, "rag_distill_enabled_pdf": True,
        "rag_distill_quality": "max", "rag_accuracy_critical_epub": True})
    assert resp.get_json()["success"] is True
    data = client.get("/get_preferences").get_json()
    assert data["rag_distill_enabled"] is True
    assert data["rag_distill_enabled_pdf"] is True
    assert data["rag_distill_quality"] == "max"
    assert data["rag_accuracy_critical_epub"] is True

    status = client.get("/rag_distill_status").get_json()
    for key in ("running", "progress", "processed", "total", "chunk", "chunks_total",
                "calls", "input_tokens", "output_tokens", "cost_usd", "estimate_only",
                "lifetime_usd", "source", "completed"):
        assert key in status, key


def test_gui_off_means_no_import():
    # Importing gui must never import rag_distill (lazy import inside the runner only).
    # Subprocess because this test module itself imports rag_distill.
    code = "import gui, sys; raise SystemExit(0 if 'rag_distill' not in sys.modules else 1)"
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(REPO_ROOT),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr


def test_gui_runner_graceful(gui_mod, monkeypatch):
    monkeypatch.setitem(sys.modules, "rag_distill", None)        # makes `import rag_distill` fail
    gui_mod._run_rag_distill([("book.epub", "book.md")], {}, source="epub",
                             accuracy_critical=False)            # must not raise
    status = gui_mod.rag_distill_status
    assert status["completed"] is True and status["running"] is False
    assert any("unavailable" in line for line in status["progress"])


# --------------------------------------------------------------------------- #
# 23. Frontmatter stripped exactly once (image-only first PDF page survives)
# --------------------------------------------------------------------------- #

SCANNED_PDF_MD = '''---
title: "Scanned Book"
author: Maria Santos
year: 2011
---

---

FIRSTPAGE unique marker paragraph about the origins of the study.

---

Second page paragraph with more prose about markets.

---

Third page paragraph with prose about institutions.
'''


def test_single_frontmatter_strip_keeps_first_pdf_page(distill_env, tmp_path):
    # A heading-poor PDF whose first page is image-only emits a body that BEGINS
    # with a '---' page separator; a second frontmatter strip used to misread it
    # as an opening fence and silently drop the entire first content page.
    md = write_md(tmp_path, SCANNED_PDF_MD, name="Scanned Book - Maria Santos 2011.md")
    result, client = run_distill(md, handler=make_handler(digest=CLEAN_DIGEST), source_kind="pdf")
    assert result.ok
    map_prompts = [c["contents"] for c in client.calls if "You are extracting knowledge" in c["contents"]]
    assert any("FIRSTPAGE" in p for p in map_prompts)            # first content page reached the map
    # The real-run plan agrees with the estimate/dry-run plan (single strip both ways).
    assert rd.estimate_run(SCANNED_PDF_MD)["chunks"] == result.chunks_total
    # Direct contract: strip_frontmatter=False treats a leading '---' as a page
    # separator, never as a frontmatter fence.
    body = rd._strip_frontmatter(SCANNED_PDF_MD)[0]
    assert body.startswith("---")
    planned = rd.plan_chunks(body, strip_frontmatter=False)
    assert any("FIRSTPAGE" in c.text for c in planned)


# --------------------------------------------------------------------------- #
# 24. Range hyphens are not minus signs; the year exemption is reachable
# --------------------------------------------------------------------------- #

def test_numeral_ranges_extract_both_endpoints():
    assert rd.extract_numerals("2008-2009") == {"2008", "2009"}
    assert rd.extract_numerals("pp. 45-52") == {"45", "52"}
    assert rd.extract_numerals("a drop of -52 units") == {"-52"}      # unary minus preserved
    assert rd.extract_numerals("range 10−12 and −42") == {"10", "12", "-42"}


RANGES_MD = '''---
title: "Ranges"
author: Maria Santos
year: 2011
---

# Chapter 1

Revenues grew 45-52 percent across 2008-2009 according to Maria Santos.
'''


def test_range_numbers_not_false_flagged(distill_env, tmp_path):
    digest = dict(CLEAN_DIGEST)
    digest["summary"] = "Maria Santos reports growth of 52 percent by 2009."
    md = write_md(tmp_path, RANGES_MD, name="Ranges - Maria Santos 2011.md")
    result, _ = run_distill(md, handler=make_handler(digest=digest))
    assert result.ok
    assert result.verification.flagged_numbers == []             # '52'/'2009' are source-backed
    companion = Path(result.companion_path).read_text(encoding="utf-8")
    assert "⚠ unverified figure" not in companion

    # Accuracy-critical: the correct block survives instead of being dropped.
    md2 = write_md(tmp_path, RANGES_MD, name="Ranges2 - Maria Santos 2011.md")
    result2, _ = run_distill(md2, handler=make_handler(digest=digest), accuracy_critical=True)
    assert result2.ok and result2.verification.dropped_items == 0
    assert "52 percent by 2009" in Path(result2.companion_path).read_text(encoding="utf-8")


YEARS_MD = '''---
title: "Years"
author: Maria Santos
---

# Chapter 1

The Model 3.1997 specification was published without fanfare.
'''


def test_year_exemption_fires(distill_env, tmp_path):
    assets = rd.extract_verbatim_assets(YEARS_MD)
    assert "1997" not in assets.numerals_source      # folded into '3.1997' by the numeral pass
    assert "1997" in assets.years_source
    assert rd._flag_unit_text("The 1997 spec mattered.", assets) == []          # rescue fires
    assert rd._flag_unit_text("The 1996 spec mattered.", assets) == ["1996"]    # absent year flags

    digest = dict(CLEAN_DIGEST)
    digest["summary"] = "Maria Santos explains the 1997 specification in detail."
    md = write_md(tmp_path, YEARS_MD, name="Years - Maria Santos 2011.md")
    result, _ = run_distill(md, handler=make_handler(digest=digest))
    assert result.ok and result.verification.flagged_numbers == []


# --------------------------------------------------------------------------- #
# 25. Firewall never scans deterministic chunk-label headings
# --------------------------------------------------------------------------- #

def _scanned_pages_md(n_pages=15):
    page = ("scanned page words about coordination and markets in prose form " * 5).strip()
    return ('---\ntitle: "Scan Study"\nauthor: Maria Santos\nyear: 2011\n---\n\n'
            + "\n\n---\n\n".join(page for _ in range(n_pages)) + "\n")


def test_firewall_skips_deterministic_headings(distill_env, tmp_path):
    claims_digest = dict(CLEAN_DIGEST)
    claims_digest["claims"] = ["Maria Santos argues markets coordinate activity."]
    src = _scanned_pages_md()

    md = write_md(tmp_path, src, name="Scan Study - Maria Santos 2011.md")
    result, _ = run_distill(md, handler=make_handler(digest=claims_digest), source_kind="pdf")
    assert result.ok
    companion = Path(result.companion_path).read_text(encoding="utf-8")
    assert '"Pages ~1–15"' in companion                # chunk-label heading rendered
    assert result.verification.flagged_numbers == []   # '15' is scaffolding, not LLM output
    assert "⚠ unverified figure" not in companion

    # Accuracy-critical: the clean claims block survives instead of being dropped.
    md2 = write_md(tmp_path, src, name="Scan Study 2 - Maria Santos 2011.md")
    result2, _ = run_distill(md2, handler=make_handler(digest=claims_digest),
                             source_kind="pdf", accuracy_critical=True)
    assert result2.ok and result2.verification.dropped_items == 0
    companion2 = Path(result2.companion_path).read_text(encoding="utf-8")
    assert "Maria Santos argues markets coordinate activity." in companion2


# --------------------------------------------------------------------------- #
# 26. Fail fast: non-retryable errors, incremental abort, server retryDelay
# --------------------------------------------------------------------------- #

class _APIErr(Exception):
    def __init__(self, msg="", code=None, retry_delay=None):
        super().__init__(msg)
        if code is not None:
            self.code = code
        if retry_delay is not None:
            self.retry_delay = retry_delay


def test_error_classification_and_server_delay():
    assert rd._is_non_retryable(_APIErr("bad request", code=400))
    assert rd._is_non_retryable(_APIErr("no auth", code=401))
    assert rd._is_non_retryable(_APIErr("forbidden", code=403))
    assert rd._is_non_retryable(_APIErr("API key not valid. Please pass a valid API key."))
    assert rd._is_non_retryable(_APIErr("403 PERMISSION_DENIED: consumer suspended"))
    assert not rd._is_non_retryable(_APIErr("429 RESOURCE_EXHAUSTED: quota exceeded"))
    assert not rd._is_non_retryable(_APIErr("503 UNAVAILABLE: overloaded", code=503))
    assert not rd._is_non_retryable(_APIErr("connection reset by peer"))

    assert rd._server_retry_delay(_APIErr("x", retry_delay=5)) == 5.0
    assert rd._server_retry_delay(
        _APIErr("429 RESOURCE_EXHAUSTED details: 'retryDelay': '18s'")) == 18.0
    assert rd._server_retry_delay(_APIErr('{"retryDelay": "2.5s"}')) == 2.5
    assert rd._server_retry_delay(_APIErr("plain failure")) is None


def test_dead_key_fails_fast(distill_env, tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "plan_chunks", _five_chunks)
    md = write_md(tmp_path, name="DK - Maria Santos 2011.md")

    def dead_key(model, contents, config, call_no):
        raise _APIErr("API key not valid. Please pass a valid API key.", code=400)

    logs = []
    result, client = run_distill(md, handler=dead_key, logs=logs)
    assert result.ok is False and result.skipped_reason == "api_error"
    assert len(client.calls) == 1                      # no retries, no further chunks
    assert result.chunks_failed == 1 and result.chunks_total == 5
    assert any("retrying cannot fix this" in line for line in logs)
    assert json.loads(rd.USAGE_LEDGER.read_text())["runs"][-1]["outcome"] == "failed"


def test_failure_ratio_aborts_incrementally(distill_env, tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "plan_chunks", _five_chunks)
    md = write_md(tmp_path, name="INC - Maria Santos 2011.md")
    result, client = run_distill(md, handler=_failing_handler(["Chapter 1", "Chapter 2"]))
    assert result.ok is False and result.skipped_reason == "too_many_failures"
    assert result.chunks_failed == 2
    # 2 failed chunks x 4 attempts each; chunks 3-5 were never attempted.
    assert len(client.calls) == 8
    assert not any("Chapter 3" in c["contents"] for c in client.calls)


def test_server_retry_delay_honored_and_capped(distill_env, tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(rd.time, "sleep", lambda s: sleeps.append(float(s)))

    def limited(msg):
        def handler(model, contents, config, call_no):
            raise _APIErr(msg)
        return handler

    md = write_md(tmp_path, name="RD1 - Maria Santos 2011.md")
    run_distill(md, handler=limited("429 RESOURCE_EXHAUSTED 'retryDelay': '7s'"))
    assert sleeps == [7.0, 7.0, 7.0]                   # max(planned 0, server 7)

    sleeps.clear()
    md2 = write_md(tmp_path, name="RD2 - Maria Santos 2011.md")
    run_distill(md2, handler=limited("429 RESOURCE_EXHAUSTED 'retryDelay': '300s'"))
    assert sleeps == [60.0, 60.0, 60.0]                # capped at MAX_RETRY_DELAY_S

    sleeps.clear()
    monkeypatch.setattr(rd, "RETRY_BACKOFF_S", (10, 10, 10))
    md3 = write_md(tmp_path, name="RD3 - Maria Santos 2011.md")
    run_distill(md3, handler=limited("429 RESOURCE_EXHAUSTED 'retryDelay': '7s'"))
    assert sleeps == [10.0, 10.0, 10.0]                # planned backoff wins when larger


# --------------------------------------------------------------------------- #
# 27. Valid JSON of the wrong shape triggers repair, never a silent empty digest
# --------------------------------------------------------------------------- #

def test_wrong_shape_json_triggers_repair(distill_env, tmp_path):
    # Top-level array: valid JSON, wrong shape => repair reprompt, not empty output.
    def array_then_good(model, contents, config, call_no):
        if "You are extracting knowledge" in contents:
            if "Re-emit valid JSON only" in contents:
                return FakeResponse(json.dumps(DIGEST))
            return FakeResponse(json.dumps([DIGEST]))
        return FakeResponse(json.dumps(REDUCE))

    md = write_md(tmp_path, name="WS1 - Maria Santos 2011.md")
    result, client = run_distill(md, handler=array_then_good)
    assert result.ok and result.chunks_failed == 0
    repairs = [c for c in client.calls if "Your previous output was invalid JSON" in c["contents"]]
    assert len(repairs) == 1
    companion = Path(result.companion_path).read_text(encoding="utf-8")
    assert DIGEST["summary"] in companion              # content did not silently vanish

    # Wrapper object: {"response": {...}} is also wrong-shape.
    def wrapper_then_good(model, contents, config, call_no):
        if "You are extracting knowledge" in contents:
            if "Re-emit valid JSON only" in contents:
                return FakeResponse(json.dumps(DIGEST))
            return FakeResponse(json.dumps({"response": DIGEST}))
        return FakeResponse(json.dumps(REDUCE))

    md2 = write_md(tmp_path, name="WS2 - Maria Santos 2011.md")
    result2, _ = run_distill(md2, handler=wrapper_then_good)
    assert result2.ok and result2.chunks_failed == 0
    assert DIGEST["summary"] in Path(result2.companion_path).read_text(encoding="utf-8")

    # Legitimately sparse digests (schema keys, empty values) are NOT wrong-shape.
    sparse = {"summary": "", "keywords": [], "claims": [], "facts_numeric": [],
              "terms": [], "qa": [], "entities": []}
    assert rd._digest_shape_ok(sparse) is True
    assert rd._digest_shape_ok([DIGEST]) is False
    assert rd._digest_shape_ok({"response": DIGEST}) is False


def test_wrong_shape_twice_becomes_placeholder(distill_env, tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "plan_chunks", _five_chunks)

    def persist_wrong(model, contents, config, call_no):
        if "You are extracting knowledge" in contents and "Chapter 1" in contents:
            return FakeResponse(json.dumps(["still", "wrong"]))
        if "You are extracting knowledge" in contents:
            return FakeResponse(json.dumps(DIGEST))
        return FakeResponse(json.dumps(REDUCE))

    md = write_md(tmp_path, name="WS3 - Maria Santos 2011.md")
    result, _ = run_distill(md, handler=persist_wrong)
    assert result.ok and result.chunks_failed == 1     # counted, not silently empty
    assert "could not be distilled" in Path(result.companion_path).read_text(encoding="utf-8")


def test_prereduce_wrong_shape_falls_back_to_raw_digests(distill_env, tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "PRE_REDUCE_TOKEN_LIMIT", 10)

    def prereduce_wrong(model, contents, config, call_no):
        if "Merge these section digests" in contents:
            return FakeResponse(json.dumps([{"bogus": 1}]))       # valid JSON, wrong shape
        if "You are extracting knowledge" in contents:
            return FakeResponse(json.dumps(DIGEST))
        return FakeResponse(json.dumps(REDUCE))

    md = write_md(tmp_path, name="WS4 - Maria Santos 2011.md")
    result, client = run_distill(md, handler=prereduce_wrong)
    assert result.ok
    reduce_calls = [c["contents"] for c in client.calls
                    if "synthesizing section digests" in c["contents"]]
    assert len(reduce_calls) == 1
    assert DIGEST["summary"] in reduce_calls[0]        # raw digests reached the reduce, not empties


# --------------------------------------------------------------------------- #
# 28. Index sections dropped from the plan; estimate output guess calibrated
# --------------------------------------------------------------------------- #

INDEX_MD = '''---
title: "Indexed"
author: Maria Santos
year: 2011
---

# Chapter 1

Body prose about markets covering the 2008-2009 crisis in detail.

# Chapter 2

More prose about institutions and coordination across many countries.

# Index

Allocation, 45-52

Bonds, 88
'''


def test_index_section_dropped_numerals_kept():
    chunks = rd.plan_chunks(INDEX_MD, min_tokens=1)
    titles = [t for c in chunks for t in c.heading_path]
    assert "Index" not in titles
    assert "Allocation" not in "\n".join(c.text for c in chunks)     # index never distilled
    assets = rd.extract_verbatim_assets(INDEX_MD)
    assert {"45", "52", "88"} <= assets.numerals_source              # firewall inventory unaffected


def test_estimate_output_guess_calibrated():
    md = "# Book\n\n" + ("many words of body text here " * 4_000).strip()
    est = rd.estimate_run(md)
    assert est["est_output_tokens"] == max(int(est["est_input_tokens"] * 0.25), 4_000)
    assert est["est_output_tokens"] > 4_000            # ratio branch exercised, not the floor
    small = rd.estimate_run("# T\n\ntiny body.")
    assert small["est_output_tokens"] == 4_000         # floor intact
