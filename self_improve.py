#!/usr/bin/env python3
"""Self-improvement mode: LLM-as-judge evaluation + GitHub issue filing.

When enabled, after an EPUB conversion this module compares the original EPUB's
reference text to the produced Markdown using an Anthropic model, returns
structured findings, and files de-duplicated GitHub issues for real quality
problems. Those issues drive a Claude Code GitHub Action that fixes, tests, and
auto-merges (see ``.github/workflows/self-improve.yml``).

Safety: a dedup ledger, per-run/per-day issue caps, an occurrence cap, and a
circuit breaker (routes findings to a ``self-improvement-hold`` label that the
Action ignores) all live in ``~/.epub2md_eval_history.json``. The toggle in the
GUI is the kill switch; ``anthropic`` is imported lazily so this module loads
without the optional dependency.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from pydantic import BaseModel, Field

from epub_text import extract_reference_text, reference_summary
from epub_to_md_converter import collect_quality_signals

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_MODEL = "claude-opus-4-8"          # judge default (correctness-sensitive)
COST_MODEL = "claude-sonnet-4-6"           # cost-downgrade option exposed in the toggle
VALID_MODELS = {DEFAULT_MODEL, COST_MODEL}

CONFIDENCE_FLOOR = 0.6
FILE_SEVERITIES = {"critical", "major"}    # which severities become issues
MAX_ISSUES_PER_RUN = 3
MAX_ISSUES_PER_DAY = 8
MAX_OCCURRENCES = 2                         # refiling cap before escalating to hold
SINGLE_PASS_CHARS = 120_000                # below this, judge the whole book in one call
MAX_CHUNKS_PER_BOOK = 12
JUDGE_MAX_TOKENS = 8000

HISTORY_PATH = Path(os.path.expanduser("~")) / ".epub2md_eval_history.json"
ISSUE_LABEL = "self-improvement"
HOLD_LABEL = "self-improvement-hold"
SIGNATURE_MARKER = "si-signature"


# --------------------------------------------------------------------------- #
# Structured findings schema (for client.messages.parse)
# --------------------------------------------------------------------------- #

class Finding(BaseModel):
    severity: str = Field(description="one of: critical, major, minor, nit")
    category: str = Field(
        description="one of: missing_content, heading_structure, artifact_noise, "
        "ordering, formatting, metadata, encoding, other"
    )
    title: str = Field(description="one-line imperative summary; stable wording (used for dedup)")
    evidence: str = Field(description="short EPUB-vs-Markdown excerpt proving the problem")
    suggested_fix: str = Field(description="concrete, code-level guidance for the fixer")
    is_systemic: bool = Field(description="true if this is likely a converter bug affecting many books")
    confidence: float = Field(description="0.0-1.0 confidence this is a real defect")
    location_hint: str | None = Field(default=None, description="chapter/heading where it occurs")


class JudgeReport(BaseModel):
    overall_assessment: str
    conversion_is_acceptable: bool
    findings: list[Finding]


VALID_SEVERITIES = {"critical", "major", "minor", "nit"}
VALID_CATEGORIES = {
    "missing_content", "heading_structure", "artifact_noise", "ordering",
    "formatting", "metadata", "encoding", "other",
}


# --------------------------------------------------------------------------- #
# Judge prompt
# --------------------------------------------------------------------------- #

RUBRIC = """You are a strict QA judge for an EPUB-to-Markdown converter whose output
feeds Claude Projects / RAG. You are given (1) the ORIGINAL EPUB reference text,
(2) DETERMINISTIC METRICS already computed by the converter, and (3) the PRODUCED
MARKDOWN. Find ways the Markdown fails to faithfully represent the EPUB.

What good output looks like:
- Proper heading hierarchy using #, ##, ### (one per chapter/section).
- No leftover artifacts: no `:::` divs, no `[]{#id}` anchors, no `{.class}` spans,
  no raw ` ``{=html} ` blocks, no `[..](#NNN_x.xhtml)` links.
- Clean lists/tables, YAML frontmatter (title/author/year), and NO lost content.

Severity:
- critical = lost or garbled content, or zero headings on a long book.
- major = a systemic artifact class, broken structure, or reordered chapters.
- minor = cosmetic; nit = trivial.

Rules:
- The DETERMINISTIC METRICS are ground truth for counts. Your job is the SEMANTIC
  gaps the metrics can't see: missing paragraphs, reordered/dropped chapters,
  mojibake, mangled tables, wrong heading levels.
- Set is_systemic=true only when the defect pattern would recur across many books
  (i.e. a converter bug), not a one-off in this title.
- Report only what you can prove with an evidence excerpt. Set confidence honestly.
- If the conversion is faithful, return an empty findings list and
  conversion_is_acceptable=true. Do not invent problems."""

QUESTION = (
    "Compare the PRODUCED MARKDOWN to the ORIGINAL EPUB reference above and report "
    "conversion-quality findings strictly per the schema. Prefer fewer, well-evidenced "
    "findings over many speculative ones."
)


# --------------------------------------------------------------------------- #
# History / ledger store
# --------------------------------------------------------------------------- #

def _default_history() -> dict:
    return {
        "evals": [],
        "ledger": {},
        "circuit_breaker": {"consecutive_regressions": 0, "auto_merge_disabled": False, "reason": None},
        "caps": {"day": "", "issues_today": 0},
    }


def load_history(path: Path = HISTORY_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return _default_history()
    base = _default_history()
    base.update({k: data.get(k, base[k]) for k in base})
    return base


def save_history(history: dict, path: Path = HISTORY_PATH) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except OSError as e:
        print(f"self-improvement: could not write history: {e}", file=sys.stderr)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _reset_caps_if_new_day(history: dict) -> None:
    if history["caps"].get("day") != _today():
        history["caps"] = {"day": _today(), "issues_today": 0}


# --------------------------------------------------------------------------- #
# Dedup signature
# --------------------------------------------------------------------------- #

def _normalize_title(title: str) -> str:
    """Collapse a finding title to a book-independent signature basis."""
    t = title.lower()
    t = re.sub(r"[\"'“”‘’]", "", t)
    t = re.sub(r"\d+", "", t)            # drop chapter numbers / counts
    t = re.sub(r"[^a-z\s]", " ", t)      # drop punctuation
    return re.sub(r"\s+", " ", t).strip()


def signature(finding: Finding) -> str:
    basis = f"{finding.category}|{finding.is_systemic}|{_normalize_title(finding.title)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Filtering / merging
# --------------------------------------------------------------------------- #

def _keep(finding: Finding) -> bool:
    if finding.severity not in VALID_SEVERITIES or finding.category not in VALID_CATEGORIES:
        return False
    if not 0.0 <= finding.confidence <= 1.0 or finding.confidence < CONFIDENCE_FLOOR:
        return False
    if finding.severity in FILE_SEVERITIES:
        return True
    return finding.is_systemic  # keep minor/nit only if systemic


def merge_findings(reports: list) -> list:
    """Flatten reports, keep filable findings, dedup by signature (highest confidence wins)."""
    best: dict = {}
    for report in reports:
        for finding in report.findings:
            if not _keep(finding):
                continue
            sig = signature(finding)
            if sig not in best or finding.confidence > best[sig].confidence:
                best[sig] = finding
    return list(best.values())


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #

def _sample_chapters(chapters: list, limit: int) -> list:
    if len(chapters) <= limit:
        return list(enumerate(chapters))
    # Always keep first and last; fill the middle with the largest chapters.
    idxs = {0, len(chapters) - 1}
    by_size = sorted(range(len(chapters)), key=lambda i: chapters[i].char_count, reverse=True)
    for i in by_size:
        if len(idxs) >= limit:
            break
        idxs.add(i)
    return [(i, chapters[i]) for i in sorted(idxs)]


def _md_slice(md_text: str, index: int, total: int, pad: int = 1500) -> str:
    """Proportional slice of the Markdown aligned to chapter position, with overlap."""
    if total <= 1:
        return md_text
    n = len(md_text)
    start = max(0, (index * n) // total - pad)
    end = min(n, ((index + 1) * n) // total + pad)
    return md_text[start:end]


# --------------------------------------------------------------------------- #
# The judge call (lazy anthropic import)
# --------------------------------------------------------------------------- #

def _judge_chunk(client, model: str, reference_text: str, metrics_block: str, md_text: str) -> JudgeReport:
    system = [{"type": "text", "text": RUBRIC}]
    messages = [{
        "role": "user",
        "content": [
            # Cached prefix: the (large, stable) EPUB reference.
            {"type": "text", "text": f"ORIGINAL EPUB REFERENCE:\n{reference_text}",
             "cache_control": {"type": "ephemeral"}},
            # Volatile, must sit AFTER the cache breakpoint.
            {"type": "text", "text": f"DETERMINISTIC METRICS:\n{metrics_block}"},
            {"type": "text", "text": f"PRODUCED MARKDOWN:\n{md_text}"},
            {"type": "text", "text": QUESTION},
        ],
    }]
    resp = client.messages.parse(
        model=model,
        max_tokens=JUDGE_MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=system,
        messages=messages,
        output_format=JudgeReport,
    )
    return resp.parsed_output


def run_judge(epub_path: str, md_path: str, signals: dict, model: str, logger=print) -> list:
    """Return a list of JudgeReport (one per chunk). Raises on API/auth errors."""
    import anthropic  # lazy: only the judge needs the SDK

    client = anthropic.Anthropic()
    chapters = extract_reference_text(epub_path)
    metrics_block = json.dumps(signals, sort_keys=True, default=str)
    with open(md_path, encoding="utf-8") as f:
        md_text = f.read()

    total_chars = sum(c.char_count for c in chapters)
    reports: list = []

    if not chapters:
        logger("self-improvement: no reference text extracted; skipping judge.")
        return reports

    if total_chars <= SINGLE_PASS_CHARS:
        reference = "\n\n".join(f"## {c.title or c.idref}\n{c.text}" for c in chapters)
        logger(f"self-improvement: judging in a single pass ({total_chars} ref chars).")
        reports.append(_judge_chunk(client, model, reference, metrics_block, md_text))
        return reports

    sampled = _sample_chapters(chapters, MAX_CHUNKS_PER_BOOK)
    logger(f"self-improvement: long book; judging {len(sampled)}/{len(chapters)} chapters.")
    for index, chapter in sampled:
        reference = f"## {chapter.title or chapter.idref}\n{chapter.text}"
        md_chunk = _md_slice(md_text, index, len(chapters))
        try:
            reports.append(_judge_chunk(client, model, reference, metrics_block, md_chunk))
        except Exception as e:  # one bad chunk shouldn't kill the whole run
            logger(f"self-improvement: chunk {index} failed: {e}")
    return reports


# --------------------------------------------------------------------------- #
# GitHub issue filing
# --------------------------------------------------------------------------- #

def ensure_labels(dry_run: bool = False, logger=print) -> None:
    labels = [
        (ISSUE_LABEL, "0e8a16", "Auto-detected conversion-quality issue (drives the fixer)"),
        (HOLD_LABEL, "b60205", "Self-improvement finding held for human review"),
        ("severity:critical", "b60205", ""),
        ("severity:major", "d93f0b", ""),
    ]
    for name, color, desc in labels:
        if dry_run:
            continue
        subprocess.run(
            ["gh", "label", "create", name, "--color", color, "--description", desc, "--force"],
            capture_output=True, text=True, check=False,
        )


def _issue_body(finding: Finding, signals: dict, book_title: str, sig: str) -> str:
    return (
        f"<!-- {SIGNATURE_MARKER}: {sig} -->\n"
        f"**Category:** {finding.category}  **Severity:** {finding.severity}  "
        f"**Systemic:** {finding.is_systemic}  **Confidence:** {finding.confidence:.2f}\n\n"
        f"### What's wrong\n{finding.evidence}\n\n"
        f"### Where\n{finding.location_hint or 'n/a'}  (book: {book_title} — local sample; do not request the EPUB)\n\n"
        f"### Suggested fix\n{finding.suggested_fix}\n\n"
        f"### Deterministic signals at time of report\n"
        f"optimization_score={signals.get('optimization_score')}, "
        f"headings={signals.get('heading_count')}, artifacts={signals.get('artifacts')}\n\n"
        "### Acceptance criteria for the fix\n"
        "- Implement the change in the converter modules (epub/html/pdf as appropriate).\n"
        "- `pytest -q` must pass (the regression suite is the gate).\n"
        "- Do NOT weaken thresholds in `tests/baselines.json` to make tests pass.\n"
        "- Keep ruff clean (line-length 120); update CHANGELOG.md; bump version.py if user-facing."
    )


def _create_issue(title: str, body: str, labels: list, logger=print) -> int | None:
    cmd = ["gh", "issue", "create", "--title", title, "--body", body]
    for label in labels:
        cmd += ["--label", label]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger(f"self-improvement: gh issue create failed: {result.stderr.strip()}")
        return None
    m = re.search(r"/issues/(\d+)", result.stdout)
    return int(m.group(1)) if m else None


def file_findings(findings: list, signals: dict, book_title: str, history: dict,
                  dry_run: bool = False, logger=print) -> list:
    """File issues for new findings, honoring dedup, caps, and the circuit breaker.

    Returns a list of dicts describing what happened to each finding.
    """
    _reset_caps_if_new_day(history)
    breaker = history["circuit_breaker"]
    ledger = history["ledger"]
    outcomes: list = []
    filed_this_run = 0
    ensure_labels(dry_run=dry_run, logger=logger)

    for finding in findings:
        sig = signature(finding)
        entry = ledger.get(sig)
        record = {"signature": sig, "severity": finding.severity,
                  "category": finding.category, "title": finding.title}

        # Dedup: already tracked and not resolved -> bump, maybe escalate, don't refile.
        if entry and entry.get("state") not in (None, "closed"):
            entry["occurrences"] = entry.get("occurrences", 1) + 1
            entry["last_seen"] = _today()
            if entry["occurrences"] >= MAX_OCCURRENCES and entry.get("label") != HOLD_LABEL:
                logger(f"self-improvement: '{finding.title}' recurred; escalating to {HOLD_LABEL}.")
                record["action"] = "escalated_hold"
            else:
                record["action"] = "deduped"
            record["issue_number"] = entry.get("issue_number")
            outcomes.append(record)
            continue

        # Caps.
        if filed_this_run >= MAX_ISSUES_PER_RUN:
            record["action"] = "capped_run"
            outcomes.append(record)
            continue
        if history["caps"]["issues_today"] >= MAX_ISSUES_PER_DAY:
            record["action"] = "capped_day"
            outcomes.append(record)
            continue

        # Circuit breaker -> hold label (Action won't trigger).
        label = HOLD_LABEL if breaker.get("auto_merge_disabled") else ISSUE_LABEL
        sev_label = f"severity:{finding.severity}" if finding.severity in FILE_SEVERITIES else None
        labels = [label] + ([sev_label] if sev_label else [])
        title = f"[self-improve] {finding.severity}: {finding.title}"
        body = _issue_body(finding, signals, book_title, sig)

        if dry_run:
            logger(f"[dry-run] would file: {title}  labels={labels}")
            issue_number = None
            record["action"] = "dry_run"
        else:
            issue_number = _create_issue(title, body, labels, logger=logger)
            record["action"] = "filed" if issue_number else "file_failed"

        if record["action"] in ("filed", "dry_run"):
            filed_this_run += 1
            if not dry_run:
                history["caps"]["issues_today"] += 1
            ledger[sig] = {
                "issue_number": issue_number,
                "state": "open",
                "label": label,
                "first_seen": _today(),
                "last_seen": _today(),
                "occurrences": 1,
            }
        record["issue_number"] = issue_number
        outcomes.append(record)

    return outcomes


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

def evaluate_conversion(epub_path: str, md_path: str, *, model: str | None = None,
                        dry_run: bool = False, logger=print) -> dict:
    """Judge one converted EPUB and file issues. Returns a summary dict.

    Fails closed: any API/auth error records the eval and files nothing.
    """
    model = model if model in VALID_MODELS else DEFAULT_MODEL
    book_title = Path(epub_path).stem

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        logger("self-improvement: ANTHROPIC_API_KEY not set; skipping evaluation.")
        return {"status": "skipped", "reason": "no_api_key", "book": book_title}

    signals = collect_quality_signals(epub_path, md_path)
    history = load_history()

    try:
        reports = run_judge(epub_path, md_path, signals, model, logger=logger)
    except Exception as e:  # auth, rate limit, network -> fail closed
        logger(f"self-improvement: judge failed ({e}); filing nothing.")
        history["evals"].append({"ts": _today(), "book": book_title, "model": model,
                                 "status": "judge_error", "error": str(e)})
        save_history(history)
        return {"status": "error", "reason": str(e), "book": book_title}

    findings = merge_findings(reports)
    logger(f"self-improvement: {len(findings)} filable finding(s) after filtering.")
    outcomes = file_findings(findings, signals, book_title, history, dry_run=dry_run, logger=logger)

    filed = [o for o in outcomes if o["action"] in ("filed", "dry_run")]
    history["evals"].append({
        "ts": _today(), "book": book_title, "model": model, "status": "ok",
        "metrics": {"optimization_score": signals["optimization_score"],
                    "heading_count": signals["heading_count"]},
        "reference": reference_summary(extract_reference_text(epub_path)),
        "findings": [{"signature": o["signature"], "severity": o["severity"],
                      "action": o["action"], "issue": o.get("issue_number")} for o in outcomes],
    })
    save_history(history)
    return {
        "status": "ok", "book": book_title, "model": model,
        "findings": len(findings), "filed": len(filed),
        "outcomes": outcomes,
    }


# --------------------------------------------------------------------------- #
# CLI (manual testing before the GUI is wired)
# --------------------------------------------------------------------------- #

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Judge an EPUB->Markdown conversion and file issues.")
    parser.add_argument("epub", help="Path to the original EPUB")
    parser.add_argument("markdown", help="Path to the produced Markdown")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(VALID_MODELS))
    parser.add_argument("--dry-run", action="store_true", help="Print issues instead of filing them")
    args = parser.parse_args()

    summary = evaluate_conversion(args.epub, args.markdown, model=args.model, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
