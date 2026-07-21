"""Regression suite — the auto-merge gate.

Layers, fastest/most-portable first:

1. **Oracle unit tests** — exercise the scoring functions on synthetic Markdown.
   Always run in CI; protect the very functions a self-improvement fix might touch.
2. **End-to-end synthetic test** — convert a generated EPUB and assert clean output.
   Always run in CI (pandoc is installed there); real teeth without committed books.
3. **Corpus floors/ceilings** — convert the local sample books. Skipped when the
   gitignored EPUBs aren't present (e.g. in CI).
4. **Baseline-tamper guard** — fails if tests/baselines.json was loosened vs main,
   so an autonomous fix cannot weaken the gate to make itself pass.
"""

import json
import shutil
import subprocess

import pytest

from epub_to_md_converter import (
    analyze_artifacts,
    calculate_optimization_score,
    collect_quality_signals,
    process_folder,
)
from tests.conftest import BASELINES_PATH, find_corpus_epub

CLEAN_MD = """---
title: "Clean Book"
---

# Chapter One

A clean paragraph of prose with no artifacts whatsoever.

## A Subsection

Another clean paragraph. Lists work too:

- item one
- item two
"""

DIRTY_MD = """# Heading {#ch1 .calibre}

``{=html}

[styled text]{.calibre3}
[more styled]{.someclass}

> ::: {}
> a quote
> :::

![image](img.jpg){.cls width="5"}
"""


# --------------------------------------------------------------------------- #
# 1. Oracle unit tests (always run)
# --------------------------------------------------------------------------- #

def test_clean_markdown_scores_high():
    artifacts = analyze_artifacts(CLEAN_MD)
    score = calculate_optimization_score(artifacts)
    assert score >= 99.0
    assert artifacts["html_blocks"] == 0
    assert artifacts["bracket_classes"] == 0
    assert artifacts["blockquote_divs"] == 0


def test_dirty_markdown_is_detected_and_penalized():
    artifacts = analyze_artifacts(DIRTY_MD)
    assert artifacts["html_blocks"] >= 1
    assert artifacts["bracket_classes"] >= 2
    assert artifacts["blockquote_divs"] >= 1
    assert artifacts["header_ids"] >= 1
    assert calculate_optimization_score(artifacts) < calculate_optimization_score(
        analyze_artifacts(CLEAN_MD)
    )


def test_optimization_score_bounds():
    # Empty content must not divide-by-zero or exceed bounds.
    assert calculate_optimization_score(analyze_artifacts("")) == 100.0
    # A pathologically dirty doc floors at 0, never negative.
    huge = "\n".join(["``{=html}"] * 500)
    assert 0.0 <= calculate_optimization_score(analyze_artifacts(huge)) <= 100.0


# --------------------------------------------------------------------------- #
# 2. End-to-end synthetic conversion (always run; real pipeline gate)
# --------------------------------------------------------------------------- #

def test_synthetic_conversion_is_clean(synthetic_epub):
    epub_path, md_path = synthetic_epub
    sig = collect_quality_signals(epub_path, md_path)
    assert sig["heading_count"] >= 3, "expected one heading per chapter"
    assert sig["optimization_score"] >= 95.0
    assert sig["md_char_count"] > 200
    for key in ("html_blocks", "bracket_classes", "blockquote_divs", "header_ids", "xhtml_links"):
        assert sig["artifacts"][key] == 0, f"unexpected {key} artifacts in clean output"


def test_gate_has_teeth(synthetic_epub):
    """Prove the oracle catches a regression: injecting artifacts must drop the score."""
    _, md_path = synthetic_epub
    with open(md_path, encoding="utf-8") as f:
        good = f.read()
    clean_score = calculate_optimization_score(analyze_artifacts(good))
    regressed = good + "\n" + "\n".join(["``{=html}", "[x]{.cls}", "> ::: {}"] * 20)
    regressed_score = calculate_optimization_score(analyze_artifacts(regressed))
    assert regressed_score < clean_score - 5, "oracle failed to penalize injected artifacts"


# --------------------------------------------------------------------------- #
# 3. Corpus floors/ceilings (skipped when EPUBs absent)
# --------------------------------------------------------------------------- #

def _corpus_keys():
    with open(BASELINES_PATH, encoding="utf-8") as f:
        return list(json.load(f).keys())


@pytest.mark.parametrize("book_key", _corpus_keys())
def test_corpus_floors(book_key, baselines, tmp_path):
    spec = baselines[book_key]
    epub = find_corpus_epub(spec["filename_glob"])
    if epub is None:
        pytest.skip(f"corpus EPUB for '{book_key}' not present")

    work = tmp_path / "in"
    work.mkdir()
    shutil.copy(str(epub), str(work))
    pairs = process_folder(str(work), str(tmp_path / "out"))
    assert pairs, f"{book_key} failed to convert"
    sig = collect_quality_signals(*pairs[0])

    assert sig["optimization_score"] >= spec["min_optimization_score"], (
        f"{book_key} optimization score regressed: "
        f"{sig['optimization_score']} < {spec['min_optimization_score']}"
    )
    assert sig["heading_count"] >= spec["min_heading_count"], (
        f"{book_key} heading count regressed: "
        f"{sig['heading_count']} < {spec['min_heading_count']}"
    )
    assert sig["md_char_count"] >= spec["min_md_chars"], (
        f"{book_key} content shrank: {sig['md_char_count']} < {spec['min_md_chars']}"
    )
    # Optional per-artifact ceilings. These are pandoc-version-sensitive (raw
    # artifact counts differ between macOS and CI's Ubuntu pandoc), so by default
    # we rely on the optimization-score floor above — which already aggregates
    # artifact density — and leave max_artifacts empty. Tighten per-artifact only
    # for counts that are stable across the environments where the gate runs.
    for artifact, ceiling in spec.get("max_artifacts", {}).items():
        assert sig["artifacts"][artifact] <= ceiling, (
            f"{book_key} {artifact} artifacts increased: "
            f"{sig['artifacts'][artifact]} > {ceiling}"
        )


# --------------------------------------------------------------------------- #
# 4. Baseline-tamper guard (the gate can't be weakened to pass)
# --------------------------------------------------------------------------- #

def test_baselines_not_loosened():
    """Fail if any floor dropped or any artifact ceiling rose versus origin/main."""
    try:
        prior = subprocess.run(
            ["git", "show", "origin/main:tests/baselines.json"],
            capture_output=True, text=True, check=False, cwd=str(BASELINES_PATH.parent.parent),
        )
    except Exception as e:  # git unavailable
        pytest.skip(f"git not available: {e}")
    if prior.returncode != 0 or not prior.stdout.strip():
        pytest.skip("no baselines.json on origin/main yet (new file)")

    old = json.loads(prior.stdout)
    with open(BASELINES_PATH, encoding="utf-8") as f:
        new = json.load(f)

    for key, old_spec in old.items():
        assert key in new, f"baseline '{key}' was removed"
        new_spec = new[key]
        for floor in ("min_optimization_score", "min_heading_count", "min_md_chars"):
            assert new_spec.get(floor, 0) >= old_spec.get(floor, 0), (
                f"{key}.{floor} was loosened: {new_spec.get(floor)} < {old_spec.get(floor)}"
            )
        for artifact, old_ceiling in old_spec.get("max_artifacts", {}).items():
            new_ceiling = new_spec.get("max_artifacts", {}).get(artifact, float("inf"))
            assert new_ceiling <= old_ceiling, (
                f"{key}.max_artifacts.{artifact} was loosened: {new_ceiling} > {old_ceiling}"
            )
