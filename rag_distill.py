#!/usr/bin/env python3
"""rag_distill.py — RAG/LLM Knowledge Optimized companion (.rag.md) via the Gemini API.

Optional feature: google-genai is lazy-imported inside functions; the base app
runs without it. distill_markdown() never raises across its public boundary.
Never modifies or replaces the full conversion .md.

Pipeline: preflight (deterministic, free) -> MAP (one cheap call per chunk) ->
REDUCE (one synthesis call) -> deterministic ASSEMBLE (the LLM never writes the
final document) -> deterministic VERIFY (table-numeral survival + hallucinated
number firewall) -> atomic WRITE -> usage LEDGER. Numbers and tables travel by
deterministic copy + verification, never trusted to the LLM.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

try:
    RAG_SUPPORT_AVAILABLE = importlib.util.find_spec("google.genai") is not None  # probe, no import
except Exception:  # find_spec raises when the parent 'google' package is absent
    RAG_SUPPORT_AVAILABLE = False

COMPANION_SUFFIX = ".rag.md"
KEY_FILE = Path.home() / ".epub2md_gemini_key"
USAGE_LEDGER = Path.home() / ".epub2md_gemini_usage.json"
DEFAULT_COST_CAP_USD = 2.00
LEDGER_RUNS_CAP = 100                      # FIFO cap on stored run rows
RETRY_BACKOFF_S = (2, 8, 30)               # per-retry delays; server retryDelay honored
MAX_RETRY_DELAY_S = 60.0                   # ceiling on any single retry sleep
MAX_BLOCK_WORDS = 500                      # atomic-block size ceiling in the companion
NORMAL_TABLE_ROW_LIMIT = 40                # normal mode appends only tables this small
PRE_REDUCE_TOKEN_LIMIT = 150_000           # digests above this get a hierarchical pre-reduce
PROMPT_OVERHEAD_TOKENS = 400               # per-chunk prompt template overhead (estimate)
MAP_MAX_OUTPUT_TOKENS = 4096
REDUCE_MAX_OUTPUT_TOKENS = 32768
FAILURE_ABORT_RATIO = 0.30                 # >30% failed chunks => abort

# quality -> (map_model, reduce_model). Pinned GA IDs only — never -latest.
QUALITY_MODELS = {
    "standard": ("gemini-3.5-flash-lite", "gemini-3.6-flash"),
    "max":      ("gemini-3.6-flash",      "gemini-3.1-pro-preview"),
}

# USD per 1M tokens; thinking billed as output; *_hi rates apply per-call when
# prompt_token_count > 200_000 (pro rows only).
PRICING = {
    "gemini-3.6-flash":       {"in": 1.50, "out": 7.50},
    "gemini-3.5-flash":       {"in": 1.50, "out": 9.00},
    "gemini-3.5-flash-lite":  {"in": 0.30, "out": 2.50},
    "gemini-2.5-flash":       {"in": 0.30, "out": 2.50},
    "gemini-2.5-pro":         {"in": 1.25, "out": 10.00, "in_hi": 2.50, "out_hi": 15.00},
    "gemini-3.1-pro-preview": {"in": 2.00, "out": 12.00, "in_hi": 4.00, "out_hi": 18.00},
}
PRO_HI_TIER_PROMPT_TOKENS = 200_000


# --------------------------------------------------------------------------- #
# Data types
# --------------------------------------------------------------------------- #

@dataclass
class Chunk:
    index: int
    heading_path: list[str]      # breadcrumb, e.g. ["Part II", "Chapter 7: ..."]
    text: str
    token_estimate: int          # len(text)//4, zero network


@dataclass
class VerbatimAssets:
    tables: list[dict]           # {"heading_path": [...], "kind": "pipe"|"html"|"figure", "raw": str}
    numerals_tables: set[str]    # normalized numerals from tables/figures
    numerals_source: set[str]    # normalized numerals from the ENTIRE source md
    years_source: set[str] = field(default_factory=set)  # 4-digit year runs in the source text


@dataclass
class UsageTotals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0       # candidates + thoughts (Google bills thinking as output)
    thought_tokens: int = 0
    cost_usd: float | None = 0.0  # None once any call hit an unpriced model
    estimate_only: bool = False


@dataclass
class VerificationReport:
    table_numerals_total: int = 0
    table_numerals_present: int = 0
    flagged_numbers: list[str] = field(default_factory=list)  # LLM-prose numerals absent from source
    dropped_items: int = 0                                    # blocks dropped (accuracy-critical)


@dataclass
class DistillResult:
    ok: bool
    companion_path: str | None
    usage: UsageTotals
    skipped_reason: str | None = None
    # 'sdk_missing'|'no_api_key'|'cost_cap'|'cost_cap_midrun'|'too_many_failures'|'api_error'|'verification_failed'|None
    verification: VerificationReport | None = None
    chunks_total: int = 0
    chunks_failed: int = 0
    error: str | None = None     # human-readable, single line, key-scrubbed


class _CostCapExceeded(Exception):
    """Internal: the live per-call cap check tripped mid-run."""


class _NonRetryableAPIError(Exception):
    """Internal: an auth/invalid-request API error that no retry can ever fix."""


# --------------------------------------------------------------------------- #
# Availability, key resolution, scrubbing
# --------------------------------------------------------------------------- #

def is_available() -> bool:
    """Cheap feature probe; no side effects, no SDK import."""
    return RAG_SUPPORT_AVAILABLE


def _scrub(msg: str, key: str | None) -> str:
    """Redact the API key from any outbound string."""
    return msg.replace(key, "•••") if key else msg


def resolve_api_key(log: Callable[[str], None] = print) -> str | None:
    """GEMINI_API_KEY env first; else ~/.epub2md_gemini_key. Never logs the key."""
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if key:
        return key
    try:
        if KEY_FILE.exists():
            if KEY_FILE.stat().st_mode & 0o077:
                log(f"Warning: {KEY_FILE} is not chmod 600")
            return KEY_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        pass
    return None


# --------------------------------------------------------------------------- #
# Token / numeral primitives
# --------------------------------------------------------------------------- #

def estimate_tokens(text: str) -> int:
    return len(text) // 4


# (?<!\d): a hyphen after a digit is a range separator ("2008-2009", "pp. 45-52"),
# never a minus sign — both endpoints must enter the inventory unsigned.
_NUMERAL_RE = re.compile(r"(?<!\d)-?\d[\d,]*\.?\d*%?")
_YEAR_RE = re.compile(r"(?<!\d)[12]\d{3}(?!\d)")       # standalone 4-digit year runs


def _normalize_numeral(raw: str) -> str:
    n = raw.replace(",", "").rstrip("%").rstrip(".")
    return n


def extract_numerals(text: str) -> set[str]:
    """Normalized numerals: commas stripped, trailing % dropped, unicode minus unified."""
    text = text.replace("−", "-")
    out = set()
    for raw in _NUMERAL_RE.findall(text):
        n = _normalize_numeral(raw)
        if n and n != "-":
            out.add(n)
    return out


# --------------------------------------------------------------------------- #
# Frontmatter + metadata (line regex, never a strict YAML parser)
# --------------------------------------------------------------------------- #

def _strip_frontmatter(md_text: str) -> tuple[str, str]:
    """Return (body, frontmatter). Only a literal --- fence pair at byte 0 counts."""
    if not md_text.startswith("---"):
        return md_text, ""
    first_nl = md_text.find("\n")
    if first_nl == -1 or md_text[:first_nl].strip() != "---":
        return md_text, ""
    m = re.search(r"^---\s*$", md_text[first_nl + 1:], re.M)
    if not m:
        return md_text, ""
    fm = md_text[first_nl + 1: first_nl + 1 + m.start()]
    body = md_text[first_nl + 1 + m.end():]
    return body.lstrip("\n"), fm


def _extract_metadata(md_text: str, md_path: str) -> tuple[str | None, str | None, str | None]:
    """(title, author, year) from frontmatter lines, else 'Title - Author Year.md' convention."""
    _, fm = _strip_frontmatter(md_text)

    def grab(key: str) -> str | None:
        m = re.search(rf"^{key}:\s*(.+?)\s*$", fm, re.M)
        if not m:
            return None
        return m.group(1).strip().strip("\"'").strip() or None

    title, author, year = grab("title"), grab("author"), None
    y = grab("year") or grab("date")
    if y:
        m = re.search(r"\d{4}", y)
        year = m.group(0) if m else None
    if not (title and author and year):
        stem = Path(md_path).stem
        m = re.match(r"^(.*?)\s+-\s+(.*?)\s+(\d{4})$", stem)
        if m:
            title = title or m.group(1)
            author = author or m.group(2)
            year = year or m.group(3)
        else:
            title = title or (stem or None)
    return title, author, year


# --------------------------------------------------------------------------- #
# Verbatim regions (tables/figures) — shared by assets, excision, and chunking
# --------------------------------------------------------------------------- #

_HEADING_RE = re.compile(r"^(#{1,4}) (.+?)\s*#*\s*$")


def _masked_line_flags(lines: list[str]) -> list[bool]:
    """True for lines inside fenced code, <table>...</table>, or <figure>...</figure>."""
    flags = [False] * len(lines)
    in_fence = in_table = in_figure = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        low = s.lower()
        if in_fence:
            flags[i] = True
            if s.startswith("```") or s.startswith("~~~"):
                in_fence = False
            continue
        if in_table:
            flags[i] = True
            if "</table>" in low:
                in_table = False
            continue
        if in_figure:
            flags[i] = True
            if "</figure>" in low:
                in_figure = False
            continue
        if s.startswith("```") or s.startswith("~~~"):
            flags[i] = True
            in_fence = True
            continue
        if low.startswith("<table"):
            flags[i] = True
            in_table = "</table>" not in low
            continue
        if low.startswith("<figure"):
            flags[i] = True
            in_figure = "</figure>" not in low
            continue
    return flags


def _find_verbatim_regions(lines: list[str]) -> list[dict]:
    """Locate pipe tables, HTML tables, figures, and any 'Figures' section.

    Returns [{"start", "end" (inclusive), "kind", "heading_path"}] in document order.
    Regions are never nested (inner ones are discarded); code fences are opaque.
    """
    regions: list[dict] = []
    crumb: list[tuple[int, str]] = []      # (level, title) breadcrumb stack
    in_fence = False
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        low = s.lower()
        if in_fence:
            if s.startswith("```") or s.startswith("~~~"):
                in_fence = False
            i += 1
            continue
        if s.startswith("```") or s.startswith("~~~"):
            in_fence = True
            i += 1
            continue
        m = _HEADING_RE.match(lines[i])
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            while crumb and crumb[-1][0] >= level:
                crumb.pop()
            crumb.append((level, title))
            if _clean_title(title) == "figures":
                # Synthetic PDF "## Figures" section: capture until the next heading <= level.
                j = i + 1
                while j < n:
                    hm = _HEADING_RE.match(lines[j])
                    if hm and len(hm.group(1)) <= level:
                        break
                    j += 1
                regions.append({"start": i, "end": j - 1, "kind": "figure",
                                "heading_path": [t for _, t in crumb]})
                i = j
                continue
            i += 1
            continue
        path = [t for _, t in crumb]
        if low.startswith("<table"):
            j = i
            while j < n and "</table>" not in lines[j].lower():
                j += 1
            regions.append({"start": i, "end": min(j, n - 1), "kind": "html", "heading_path": path})
            i = min(j, n - 1) + 1
            continue
        if low.startswith("<figure"):
            j = i
            while j < n and "</figure>" not in lines[j].lower():
                j += 1
            regions.append({"start": i, "end": min(j, n - 1), "kind": "figure", "heading_path": path})
            i = min(j, n - 1) + 1
            continue
        if s.startswith("|"):
            j = i
            while j < n and lines[j].strip().startswith("|"):
                j += 1
            if j - i >= 2:                 # a real pipe table, not a stray | line
                regions.append({"start": i, "end": j - 1, "kind": "pipe", "heading_path": path})
            i = j
            continue
        i += 1
    return regions


def extract_verbatim_assets(md_text: str) -> VerbatimAssets:
    """Byte-exact tables/figures with nearest-heading provenance + numeral inventories."""
    body, _ = _strip_frontmatter(md_text)
    lines = body.split("\n")
    tables = []
    for r in _find_verbatim_regions(lines):
        raw = "\n".join(lines[r["start"]: r["end"] + 1])
        tables.append({"heading_path": list(r["heading_path"]), "kind": r["kind"], "raw": raw})
    numerals_tables: set[str] = set()
    for t in tables:
        numerals_tables |= extract_numerals(t["raw"])
    return VerbatimAssets(tables=tables, numerals_tables=numerals_tables,
                          numerals_source=extract_numerals(md_text),
                          years_source=set(_YEAR_RE.findall(md_text.replace("−", "-"))))


def _excise_verbatim_regions(text: str) -> str:
    """Accuracy-critical: replace tables/figures with placeholders so the LLM never carries tabular numbers."""
    lines = text.split("\n")
    for r in reversed(_find_verbatim_regions(lines)):
        first_row = lines[r["start"]].strip() or r["kind"]
        label = "FIGURE" if r["kind"] == "figure" else "TABLE"
        placeholder = f"[{label}: {first_row[:60]} — reproduced verbatim in the appendix]"
        lines[r["start"]: r["end"] + 1] = [placeholder]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Chunk planning
# --------------------------------------------------------------------------- #

_DROP_SECTION_TITLES = {"table of contents", "pages", "guide", "landmarks", "figures", "index"}


def _clean_title(title: str | None) -> str:
    return re.sub(r"[*_`:#]+", "", title or "").strip().lower()


class _Section:
    __slots__ = ("level", "title", "own_lines", "children")

    def __init__(self, level: int, title: str | None):
        self.level = level
        self.title = title
        self.own_lines: list[str] = []
        self.children: list[_Section] = []


def _prune_dropped(node: _Section) -> None:
    node.children = [c for c in node.children if _clean_title(c.title) not in _DROP_SECTION_TITLES]
    for c in node.children:
        _prune_dropped(c)


def _render_subtree(node: _Section, own_text: str) -> str:
    parts = [own_text] if own_text else []
    for child in node.children:
        parts.append("#" * child.level + " " + (child.title or ""))
        sub = _render_subtree(child, "\n".join(child.own_lines).strip())
        if sub:
            parts.append(sub)
    return "\n\n".join(p for p in parts if p)


def _pack_paragraphs(text: str, target_tokens: int) -> list[tuple[str, int]]:
    paras = [p for p in re.split(r"\n\n+", text) if p.strip()]
    parts: list[tuple[str, int]] = []
    cur: list[str] = []
    cur_tk = 0
    for p in paras:
        ptk = estimate_tokens(p)
        if cur and cur_tk + ptk > target_tokens:
            parts.append(("\n\n".join(cur), cur_tk))
            cur, cur_tk = [], 0
        cur.append(p)
        cur_tk += ptk
    if cur:
        parts.append(("\n\n".join(cur), cur_tk))
    return parts


def _emit_section(node: _Section, path: list[str], preamble: str, out: list[Chunk],
                  target_tokens: int, max_tokens: int) -> None:
    own = "\n".join(node.own_lines).strip()
    if preamble:
        own = f"{preamble}\n\n{own}" if own else preamble
    full = _render_subtree(node, own)
    tk = estimate_tokens(full)
    if tk > target_tokens and node.children:
        # Descend: parent preamble stays with the first child; parent title prepends the path.
        for i, child in enumerate(node.children):
            _emit_section(child, path + [child.title or ""], own if i == 0 else "",
                          out, target_tokens, max_tokens)
        return
    if not full.strip():
        return
    label_path = path if path else ["Document"]
    if tk > max_tokens:
        for n, (ptext, ptk) in enumerate(_pack_paragraphs(full, target_tokens), 1):
            out.append(Chunk(0, label_path[:-1] + [f"{label_path[-1]} (part {n})"], ptext, ptk))
    else:
        out.append(Chunk(0, list(label_path), full, tk))


def _merge_pair(a: Chunk, b: Chunk) -> Chunk:
    joined = f"{a.heading_path[-1]} + {b.heading_path[-1]}"
    return Chunk(a.index, a.heading_path[:-1] + [joined],
                 f"{a.text}\n\n{b.text}", a.token_estimate + b.token_estimate)


def _merge_tiny(chunks: list[Chunk], min_tokens: int) -> list[Chunk]:
    merged: list[Chunk] = []
    for c in chunks:
        if merged and merged[-1].token_estimate + c.token_estimate < min_tokens:
            merged[-1] = _merge_pair(merged[-1], c)
        else:
            merged.append(c)
    return merged


def _plan_by_headings(lines: list[str], heading_flags: list[bool], target_tokens: int,
                      max_tokens: int, min_tokens: int) -> list[Chunk]:
    root = _Section(0, None)
    stack = [root]
    for line, is_head in zip(lines, heading_flags):
        if is_head:
            m = _HEADING_RE.match(line)
            level, title = len(m.group(1)), m.group(2).strip()
            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop()
            parent = stack[-1]
            # Non-monotonic hierarchies: orphan #### clamps to parent level + 1.
            node = _Section(min(level, parent.level + 1), title)
            parent.children.append(node)
            stack.append(node)
        else:
            stack[-1].own_lines.append(line)
    _prune_dropped(root)
    out: list[Chunk] = []
    _emit_section(root, [], "", out, target_tokens, max_tokens)
    return _merge_tiny(out, min_tokens)


def _plan_headingless(lines: list[str], masked: list[bool], target_tokens: int) -> list[Chunk]:
    """Greedy paragraph packing with one-paragraph overlap; 'Pages ~N–M' paths from page-break rules."""
    paras: list[tuple[str, int]] = []
    page = 1
    saw_marker = False
    buf: list[str] = []
    buf_page = 1

    def flush() -> None:
        nonlocal buf
        text = "\n".join(buf).strip()
        if text:
            paras.append((text, buf_page))
        buf = []

    for ln, m in zip(lines, masked):
        s = ln.strip()
        if not m and s == "---":           # PDF page-break rule: a separator, never a heading
            flush()
            page += 1
            saw_marker = True
            continue
        if not m and s == "":
            flush()
            continue
        if not buf:
            buf_page = page
        buf.append(ln)
    flush()
    if not paras:
        return []

    windows: list[list[tuple[str, int]]] = []
    cur: list[tuple[str, int]] = []
    cur_tk = 0
    for ptext, ppage in paras:
        ptk = estimate_tokens(ptext)
        if cur and cur_tk + ptk > target_tokens:
            windows.append(cur)
            overlap = cur[-1]              # overlap = last paragraph of the previous window
            cur = [overlap]
            cur_tk = estimate_tokens(overlap[0])
        cur.append((ptext, ppage))
        cur_tk += ptk
    if cur:
        windows.append(cur)

    chunks = []
    for k, win in enumerate(windows, 1):
        text = "\n\n".join(p for p, _ in win)
        pages = [pg for _, pg in win]
        path = [f"Pages ~{min(pages)}–{max(pages)}"] if saw_marker else [f"Section {k}"]
        chunks.append(Chunk(0, path, text, estimate_tokens(text)))
    return chunks


def plan_chunks(md_text: str, *, source_type: str = "epub",
                target_tokens: int = 24_000, max_tokens: int = 32_000,
                min_tokens: int = 1_500, max_chunks: int = 40,
                strip_frontmatter: bool = True) -> list[Chunk]:
    """Heading-aware chunk plan (deterministic, zero network).

    Pass strip_frontmatter=False when md_text is an already-stripped body: a
    second strip would misread a leading PDF page separator (image-only first
    page) as a frontmatter fence and silently drop the first content page.
    """
    body = _strip_frontmatter(md_text)[0] if strip_frontmatter else md_text
    lines = body.split("\n")
    masked = _masked_line_flags(lines)
    heading_flags = [bool(_HEADING_RE.match(ln)) and not m for ln, m in zip(lines, masked)]
    headingless = sum(heading_flags) < 3   # pdfplumber/OCR output: too few headings to trust

    def build(tgt: int, mx: int) -> list[Chunk]:
        if headingless:
            return _plan_headingless(lines, masked, tgt)
        return _plan_by_headings(lines, heading_flags, tgt, mx, min_tokens)

    chunks = build(target_tokens, max_tokens)
    tries = 0
    while len(chunks) > max_chunks and tries < 5:
        total = sum(c.token_estimate for c in chunks) or 1
        target_tokens = max(target_tokens + 1, -(-total // max_chunks))
        max_tokens = max(max_tokens, (target_tokens * 4) // 3)
        chunks = build(target_tokens, max_tokens)
        tries += 1
    while len(chunks) > max_chunks:        # deterministic last resort: merge smallest adjacent pair
        i = min(range(len(chunks) - 1),
                key=lambda j: chunks[j].token_estimate + chunks[j + 1].token_estimate)
        chunks[i:i + 2] = [_merge_pair(chunks[i], chunks[i + 1])]
    for i, c in enumerate(chunks):
        c.index = i
    return chunks


# --------------------------------------------------------------------------- #
# Cost model
# --------------------------------------------------------------------------- #

def compute_call_cost(model: str, prompt_tokens: int,
                      output_tokens_incl_thoughts: int) -> tuple[float | None, bool]:
    """(usd, is_estimate). Per-call >200k hi-tier for pro rows. Unknown model -> (None, True)."""
    rates = PRICING.get(model)
    if not rates:
        return None, True
    hi = prompt_tokens > PRO_HI_TIER_PROMPT_TOKENS and "in_hi" in rates
    in_rate = rates["in_hi"] if hi else rates["in"]
    out_rate = rates["out_hi"] if hi else rates["out"]
    return (prompt_tokens * in_rate + output_tokens_incl_thoughts * out_rate) / 1e6, False


def _estimate_from_chunks(chunks: list[Chunk], quality: str) -> dict:
    map_model, reduce_model = QUALITY_MODELS.get(quality, QUALITY_MODELS["standard"])
    est_in = sum(c.token_estimate for c in chunks) + PROMPT_OVERHEAD_TOKENS * max(len(chunks), 1)
    # Output guess = 25% of input, floor 4k — calibrated against a real standard-
    # quality book run that produced 27% (the original 12% guess ran well under).
    est_out = max(int(est_in * 0.25), 4_000)
    map_cost, _ = compute_call_cost(map_model, est_in, est_out)
    reduce_cost, _ = compute_call_cost(reduce_model, est_out, max(int(est_out * 0.25), 4_000))
    cost = None if (map_cost is None or reduce_cost is None) else round(map_cost + reduce_cost, 4)
    return {"chunks": len(chunks), "est_input_tokens": est_in,
            "est_output_tokens": est_out, "est_cost_usd": cost}


def estimate_run(md_text: str, quality: str = "standard") -> dict:
    """Deterministic plan + cost estimate; zero network."""
    return _estimate_from_chunks(plan_chunks(md_text), quality)


# --------------------------------------------------------------------------- #
# Usage ledger — ~/.epub2md_gemini_usage.json
# --------------------------------------------------------------------------- #

def _atomic_write_text(path: Path, text: str) -> None:
    """tmp + os.replace, chmod 600; the tmp never survives a failure."""
    tmp = Path(str(path) + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def load_usage_ledger() -> dict:
    """{} default; a corrupt file is renamed .bak, fresh start, one warning."""
    try:
        with open(USAGE_LEDGER, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("ledger root is not a JSON object")
        return data
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, ValueError, OSError):
        bak = Path(str(USAGE_LEDGER) + ".bak")
        try:
            os.replace(USAGE_LEDGER, bak)
            print(f"rag-distill: corrupt usage ledger rotated to {bak.name}; starting fresh",
                  file=sys.stderr)
        except OSError:
            pass
        return {}


def record_run(usage: UsageTotals, *, source_kind: str, file_name: str, quality: str,
               models: list[str], accuracy_critical: bool, outcome: str,
               chunks: int, chunks_failed: int) -> dict:
    """Append a run row + update lifetime totals. Never fatal to distillation."""
    ledger = load_usage_ledger()
    ledger["version"] = ledger.get("version", 1)
    prev = ledger.get("lifetime") or {}
    life = {
        "calls": int(prev.get("calls", 0)) + usage.calls,
        "input_tokens": int(prev.get("input_tokens", 0)) + usage.input_tokens,
        "output_tokens": int(prev.get("output_tokens", 0)) + usage.output_tokens,
        "thought_tokens": int(prev.get("thought_tokens", 0)) + usage.thought_tokens,
        "cost_usd": float(prev.get("cost_usd") or 0.0),
        "uncosted_calls": int(prev.get("uncosted_calls", 0)),
    }
    if usage.cost_usd is not None:
        life["cost_usd"] = round(life["cost_usd"] + usage.cost_usd, 6)
    if usage.estimate_only:
        life["uncosted_calls"] += usage.calls
    ledger["lifetime"] = life
    runs = ledger.get("runs") or []
    runs.append({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": source_kind,
        "file": file_name, "quality": quality, "models": list(models),
        "accuracy_critical": accuracy_critical, "calls": usage.calls,
        "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
        "thought_tokens": usage.thought_tokens, "cost_usd": usage.cost_usd,
        "estimate_only": usage.estimate_only, "chunks": chunks,
        "chunks_failed": chunks_failed, "outcome": outcome,
    })
    ledger["runs"] = runs[-LEDGER_RUNS_CAP:]   # FIFO cap; lifetime totals stay monotonic
    try:
        _atomic_write_text(USAGE_LEDGER, json.dumps(ledger, indent=2) + "\n")
    except Exception as e:
        print(f"rag-distill: could not write usage ledger: {e}", file=sys.stderr)
    return life


def format_usage_line(run: UsageTotals, lifetime: dict) -> str:
    lifetime = lifetime or {}
    life_part = f"lifetime ${(lifetime.get('cost_usd') or 0.0):.2f}"
    uncosted = int(lifetime.get("uncosted_calls") or 0)
    if uncosted:
        life_part += f" + {uncosted} uncosted calls"
    if run.estimate_only or run.cost_usd is None:
        run_part = "cost unknown (unpriced model)"
    else:
        run_part = f"${run.cost_usd:.4f}"
    return (f"LLM usage: {run.calls} calls, {run.input_tokens:,} in / "
            f"{run.output_tokens:,} out tok, {run_part} this run — {life_part}")


# --------------------------------------------------------------------------- #
# Gemini client + calls (SDK imports live ONLY here, and only on the real path)
# --------------------------------------------------------------------------- #

def _get_client(api_key: str) -> Any:
    from google import genai  # lazy: the ONLY SDK import in this module
    return genai.Client(api_key=api_key)


def _make_config(model: str, *, thinking: str, max_output_tokens: int, typed: bool) -> Any:
    # Gemini 3.x uses discrete thinking_level; 2.5-era models use thinking_budget=0.
    thinking_cfg: dict = {"thinking_budget": 0} if model.startswith("gemini-2.5") \
        else {"thinking_level": thinking}
    if typed:
        from google.genai import types
        return types.GenerateContentConfig(
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(**thinking_cfg),
            max_output_tokens=max_output_tokens,
        )
    # Injected test clients get a plain dict (also accepted by the real SDK).
    return {"response_mime_type": "application/json", "thinking_config": thinking_cfg,
            "max_output_tokens": max_output_tokens}


def _accumulate_usage(usage: UsageTotals, model: str, resp: Any, status: dict | None) -> None:
    """Fold response.usage_metadata into the running totals (after EVERY call)."""
    um = getattr(resp, "usage_metadata", None)
    prompt_t = int(getattr(um, "prompt_token_count", 0) or 0)
    cand_t = int(getattr(um, "candidates_token_count", 0) or 0)
    thought_t = int(getattr(um, "thoughts_token_count", 0) or 0)
    usage.calls += 1
    usage.input_tokens += prompt_t
    usage.output_tokens += cand_t + thought_t      # Google bills thinking as output
    usage.thought_tokens += thought_t
    cost, _ = compute_call_cost(model, prompt_t, cand_t + thought_t)
    if cost is None:
        usage.cost_usd = None                      # never fabricate a dollar figure
        usage.estimate_only = True
    elif usage.cost_usd is not None:
        usage.cost_usd = round(usage.cost_usd + cost, 6)
    if status is not None:
        status["calls"] = usage.calls
        status["input_tokens"] = usage.input_tokens
        status["output_tokens"] = usage.output_tokens
        status["cost_usd"] = usage.cost_usd if usage.cost_usd is not None else 0.0
        status["estimate_only"] = usage.estimate_only


_NON_RETRYABLE_STATUS = {400, 401, 403}
_NON_RETRYABLE_MSG_RE = re.compile(
    r"API[ _]?KEY[ _]?INVALID|API key not valid|PERMISSION_DENIED|UNAUTHENTICATED|INVALID_ARGUMENT",
    re.I)


def _is_non_retryable(e: Exception) -> bool:
    """400/401/403-style API errors (bad key, permission, invalid request):
    retrying the identical request cannot succeed — fail fast instead."""
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    try:
        if int(code) in _NON_RETRYABLE_STATUS:
            return True
    except (TypeError, ValueError):
        pass
    return bool(_NON_RETRYABLE_MSG_RE.search(str(e)))


def _server_retry_delay(e: Exception) -> float | None:
    """Server-advised retry delay: a retry_delay attribute, or the 429 RetryInfo
    'retryDelay': '18s' detail embedded in the error message."""
    attr = getattr(e, "retry_delay", None)
    if attr is not None:
        try:
            return float(attr.total_seconds() if hasattr(attr, "total_seconds") else attr)
        except (TypeError, ValueError):
            pass
    m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)\s*s?", str(e), re.I)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _call_model(client: Any, model: str, contents: str, config: Any, usage: UsageTotals,
                status: dict | None, _log: Callable[[str], None], cost_cap: float) -> Any:
    """One logical call: retries with backoff, usage accumulation, live cap check.

    Non-retryable errors raise _NonRetryableAPIError immediately (no retries);
    retry sleeps honor max(server retryDelay, planned backoff), capped at 60s."""
    last_error: Exception | None = None
    for attempt in range(1 + len(RETRY_BACKOFF_S)):
        try:
            resp = client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as e:
            if _is_non_retryable(e):
                raise _NonRetryableAPIError(f"non-retryable API error from {model}: {e}") from e
            last_error = e
            if attempt < len(RETRY_BACKOFF_S):
                delay = float(RETRY_BACKOFF_S[attempt])
                server_delay = _server_retry_delay(e)              # honor 429 RetryInfo
                if server_delay is not None:
                    delay = max(delay, server_delay)
                delay = min(delay, MAX_RETRY_DELAY_S)
                _log(f"RAG distill: {model} call failed ({e}); "
                     f"retry {attempt + 1}/{len(RETRY_BACKOFF_S)} in {delay:g}s")
                if delay:
                    time.sleep(delay)
            continue
        _accumulate_usage(usage, model, resp, status)
        if usage.cost_usd is not None and usage.cost_usd > cost_cap:
            raise _CostCapExceeded(
                f"run cost ${usage.cost_usd:.4f} exceeds the ${cost_cap:.2f} cap")
        return resp
    raise last_error if last_error is not None else RuntimeError("model call failed")


def _parse_json_text(text: str) -> Any:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    return json.loads(t)


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #

_MAP_SCHEMA = (
    'Return ONLY JSON: { "summary": "120-250 words, self-contained",\n'
    '  "keywords": [5-10], "claims": ["one factual claim per string, <=30 words"],\n'
    '  "facts_numeric": ["claims containing numbers/dates/quantities, copied VERBATIM"],\n'
    '  "terms": [{"term","definition"}], "qa": [{"q","a"}]  // 2-5 pairs,\n'
    '  "entities": [{"name","kind","one_liner"}] }'
)

_MAP_RULES = (
    "RULES: (1) Use ONLY the text below — if it is not stated there, it does not exist.\n"
    "(2) Every string must be understandable with ZERO surrounding context: no pronouns\n"
    'with external referents, never "the author"/"this chapter" — name the person/chapter;\n'
    "full names at first mention per string. (3) Copy all numbers, units, dates\n"
    "character-for-character; never round, convert, or invent. (4) qa questions must be\n"
    "phrased as a reader's question naming full entities. (5) Empty arrays are correct\n"
    "for non-substantive sections; padding is an error. (6) Do not summarize tables or\n"
    "figures — they are handled separately."
)

_ACCURACY_ADDENDUM = (
    "This document is accuracy-critical. If unsure whether a statement is supported, "
    "omit it. Omission is always correct; unsupported inclusion is failure."
)


def _map_prompt(chunk: Chunk, meta: tuple, accuracy_critical: bool) -> str:
    title, author, year = meta
    head = (f'You are extracting knowledge from a section of the book '
            f'"{title or "Unknown"}" by {author or "an unknown author"} ({year or "n.d."}).\n'
            f'Section location: {" > ".join(chunk.heading_path)}.\n')
    ac = f"\n{_ACCURACY_ADDENDUM}" if accuracy_critical else ""
    return f"{head}{_MAP_SCHEMA}\n{_MAP_RULES}{ac}\n---\n{chunk.text}"


def _reduce_prompt(digests_json: str, meta: tuple) -> str:
    title, author, year = meta
    return (
        f'You are synthesizing section digests of the book "{title or "Unknown"}" by '
        f'{author or "an unknown author"} ({year or "n.d."}) into book-level knowledge.\n'
        'Return ONLY JSON: {"executive_summary", "thesis": [numbered claims], '
        '"themes": [{"theme","synthesis"(<=250 words, chapters/entities named),"keywords"}], '
        '"glossary": [merged canonical {"term","definition"} entries], '
        '"question_bank": [20-60 deduped {"q","a"}], "entity_index": [deduped '
        '{"name","kind","one_liner"}]}\n'
        f"{_MAP_RULES}\n"
        "You may select, merge, dedupe, reorder — never introduce a fact, number, or name "
        "absent from the inputs; numbers pass through character-for-character; when a "
        "concept spans chapters produce ONE canonical entry, varying wording rather than "
        "repeating verbatim.\n---\n" + digests_json
    )


def _prereduce_prompt(group_json: str, meta: tuple) -> str:
    title, _, _ = meta
    return (
        f'Merge these section digests of "{title or "Unknown"}" into ONE digest of the '
        "same JSON shape (summary, keywords, claims, facts_numeric, terms, qa, entities). "
        "Dedupe aggressively; never introduce a fact, number, or name absent from the "
        "inputs. Return ONLY JSON.\n---\n" + group_json
    )


# --------------------------------------------------------------------------- #
# Map / reduce orchestration
# --------------------------------------------------------------------------- #

_DIGEST_DEFAULTS: dict = {"summary": "", "keywords": [], "claims": [], "facts_numeric": [],
                          "terms": [], "qa": [], "entities": []}


def _normalize_digest(data: Any) -> dict:
    out = {k: (v.copy() if isinstance(v, list) else v) for k, v in _DIGEST_DEFAULTS.items()}
    if isinstance(data, dict):
        for k, default in _DIGEST_DEFAULTS.items():
            v = data.get(k)
            if isinstance(default, str) and isinstance(v, str):
                out[k] = v
            elif isinstance(default, list) and isinstance(v, list):
                out[k] = v
    return out


def _digest_shape_ok(data: Any) -> bool:
    """A digest must be an object carrying at least one schema key of the right
    type. Top-level arrays and wrapper objects ({"response": {...}}) fail here so
    they hit the repair reprompt instead of silently normalizing to empty.
    Legitimately sparse digests (empty arrays, empty summary) still pass."""
    if not isinstance(data, dict):
        return False
    for k, default in _DIGEST_DEFAULTS.items():
        if isinstance(data.get(k), type(default)):
            return True
    return False


def _placeholder_digest(chunk: Chunk) -> dict:
    h = " > ".join(chunk.heading_path)
    d = _normalize_digest(None)
    d["summary"] = f"[Section '{h}' could not be distilled — refer to the full text.]"
    d["_failed"] = True
    return d


def _map_chunk(client: Any, model: str, chunk: Chunk, meta: tuple, config: Any,
               usage: UsageTotals, status: dict | None, _log: Callable[[str], None],
               accuracy_critical: bool, cost_cap: float) -> dict:
    prompt = _map_prompt(chunk, meta, accuracy_critical)
    resp = _call_model(client, model, prompt, config, usage, status, _log, cost_cap)
    try:
        data = _parse_json_text(resp.text)
        if not _digest_shape_ok(data):
            raise ValueError("valid JSON but not a digest object with the required keys")
        return _normalize_digest(data)
    except ValueError as e:
        _log(f"RAG distill: chunk {chunk.index + 1} returned invalid or wrong-shape JSON; "
             "sending one repair reprompt")
        repair = f"{prompt}\n\nYour previous output was invalid JSON: {e}. Re-emit valid JSON only."
        resp2 = _call_model(client, model, repair, config, usage, status, _log, cost_cap)
        data2 = _parse_json_text(resp2.text)
        if not _digest_shape_ok(data2):
            raise ValueError("repair reprompt also returned a wrong-shape digest") from e
        return _normalize_digest(data2)


_REDUCE_DEFAULTS: dict = {"executive_summary": "", "thesis": [], "themes": [],
                          "glossary": [], "question_bank": [], "entity_index": []}


def _normalize_reduce(data: Any) -> dict:
    out = {k: (v.copy() if isinstance(v, list) else v) for k, v in _REDUCE_DEFAULTS.items()}
    if isinstance(data, dict):
        for k, default in _REDUCE_DEFAULTS.items():
            v = data.get(k)
            if isinstance(default, str) and isinstance(v, str):
                out[k] = v
            elif isinstance(default, list) and isinstance(v, list):
                out[k] = v
    return out


def _run_reduce(client: Any, quality: str, digests: list[dict], meta: tuple,
                usage: UsageTotals, status: dict | None, _log: Callable[[str], None],
                cost_cap: float, typed: bool) -> dict:
    map_model, reduce_model = QUALITY_MODELS.get(quality, QUALITY_MODELS["standard"])
    payload = json.dumps(digests, separators=(",", ":"), ensure_ascii=False)
    if estimate_tokens(payload) > PRE_REDUCE_TOKEN_LIMIT:
        # Hierarchical pre-reduce: merge groups of ~10 digests via the cheap map model.
        _log("RAG distill: digests exceed the reduce budget; running hierarchical pre-reduce")
        pre_config = _make_config(map_model, thinking="low",
                                  max_output_tokens=MAP_MAX_OUTPUT_TOKENS, typed=typed)
        merged: list[dict] = []
        for i in range(0, len(digests), 10):
            group = digests[i:i + 10]
            gp = _prereduce_prompt(json.dumps(group, separators=(",", ":"), ensure_ascii=False), meta)
            try:
                resp = _call_model(client, map_model, gp, pre_config, usage, status, _log, cost_cap)
                data = _parse_json_text(resp.text)
                if not _digest_shape_ok(data):
                    raise ValueError("pre-reduce returned a wrong-shape digest")
                merged.append(_normalize_digest(data))
            except (_CostCapExceeded, _NonRetryableAPIError):
                raise
            except Exception:
                merged.extend(group)       # pre-reduce is an optimization; fall back to raw digests
        digests = merged
        payload = json.dumps(digests, separators=(",", ":"), ensure_ascii=False)
    config = _make_config(reduce_model, thinking="medium",
                          max_output_tokens=REDUCE_MAX_OUTPUT_TOKENS, typed=typed)
    prompt = _reduce_prompt(payload, meta)
    resp = _call_model(client, reduce_model, prompt, config, usage, status, _log, cost_cap)
    try:
        return _normalize_reduce(_parse_json_text(resp.text))
    except ValueError as e:
        # One retry with halved section budgets (covers truncated/invalid output).
        _log("RAG distill: reduce output invalid/truncated; retrying with halved budgets")
        retry = (f"{prompt}\n\nYour previous output was invalid or truncated JSON: {e}. "
                 "Re-emit valid JSON only, halving the length budget of every section.")
        resp2 = _call_model(client, reduce_model, retry, config, usage, status, _log, cost_cap)
        return _normalize_reduce(_parse_json_text(resp2.text))


# --------------------------------------------------------------------------- #
# Assembly (deterministic Python — the LLM never writes the final document)
# --------------------------------------------------------------------------- #

_PRONOUN_RE = re.compile(r"^(?:He|She|It|They)\b|^(?:This|These)\s")
_ORDINAL_RE = re.compile(r"^\s*\d+\.\s", re.M)


def _pronoun_lint(body: str, subject: str) -> str:
    """Blocks must not lead with a context-dependent pronoun; prefix the subject name."""
    if _PRONOUN_RE.match(body):
        return f"{subject}: {body}"
    return body


def _split_long_block(text: str, max_words: int = MAX_BLOCK_WORDS) -> list[str]:
    """Split a block over the word ceiling at sentence (or line) boundaries."""
    if len(text.split()) <= max_words:
        return [text]
    lines = text.split("\n")
    if len(lines) > 1:                     # bullet/numbered bodies: split at line boundaries
        pieces, cur, w = [], [], 0
        for ln in lines:
            lw = len(ln.split())
            if cur and w + lw > max_words:
                pieces.append("\n".join(cur))
                cur, w = [], 0
            cur.append(ln)
            w += lw
        if cur:
            pieces.append("\n".join(cur))
        return pieces
    sentences = re.split(r"(?<=[.!?])\s+", text)
    pieces, cur, w = [], [], 0
    for s in sentences:
        sw = len(s.split())
        if cur and w + sw > max_words:
            pieces.append(" ".join(cur))
            cur, w = [], 0
        cur.append(s)
        w += sw
    if cur:
        pieces.append(" ".join(cur))
    final: list[str] = []
    for p in pieces:                       # a single runaway sentence: hard word split
        pw = p.split()
        while len(pw) > max_words:
            final.append(" ".join(pw[:max_words]))
            pw = pw[max_words:]
        if pw:
            final.append(" ".join(pw))
    return final or [text]


def _footer(meta: tuple, loc: str) -> str:
    title, author, year = meta
    bits = title or "Unknown title"
    if author:
        bits += f" — {author}"
    if year:
        bits += f" ({year})"
    return f"*[Source: {bits}, {loc}]*"


def _enforce_block(body: str, footer: str) -> str:
    """Atomic blocks <=500 words, each with its own provenance footer."""
    return "\n\n".join(f"{p}\n\n{footer}" for p in _split_long_block(body))


def _unit(body: str, loc: str, heading: str | None = None,
          placeholder: bool = False, heading_llm: bool = True) -> dict:
    # heading_llm=False marks a code-generated heading (template + deterministic
    # chunk label such as 'Pages ~120–135'); the numeral firewall must not scan it.
    return {"heading": heading, "body": body, "loc": loc, "placeholder": placeholder,
            "heading_llm": heading_llm}


def _question_shape(q: str) -> str:
    q = (q or "").strip()
    return q if q.endswith("?") else (q.rstrip("?.! ") + "?")


def _dedup_keep_order(items: list[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        k = it.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(it.strip())
    return out


def _glossary_entries(reduce_out: dict, digests: list[dict]) -> list[tuple[str, str]]:
    raw = reduce_out.get("glossary") or []
    if not raw:
        raw = [t for d in digests for t in d.get("terms", [])]
    entries: dict[str, str] = {}
    for e in raw:
        if isinstance(e, dict) and e.get("term"):
            entries.setdefault(str(e["term"]).strip(), str(e.get("definition", "")).strip())
    return sorted(entries.items(), key=lambda kv: kv[0].lower())


def _entity_entries(reduce_out: dict, digests: list[dict]) -> list[dict]:
    raw = reduce_out.get("entity_index") or [e for d in digests for e in d.get("entities", [])]
    seen, out = set(), []
    for e in raw:
        if isinstance(e, dict) and e.get("name") and str(e["name"]).lower() not in seen:
            seen.add(str(e["name"]).lower())
            out.append(e)
    return out


def _asset_preview(asset: dict) -> str:
    first = asset["raw"].split("\n", 1)[0].strip().strip("|").strip()
    return (first[:40] + "…") if len(first) > 40 else (first or asset["kind"])


def _table_row_count(asset: dict) -> int:
    if asset["kind"] == "pipe":
        return asset["raw"].count("\n") + 1
    if asset["kind"] == "html":
        return max(asset["raw"].lower().count("<tr"), 1)
    return 1                               # figures are always appended


def _select_appendix_assets(assets: VerbatimAssets, accuracy_critical: bool) -> list[dict]:
    if accuracy_critical:
        return list(assets.tables)         # accuracy-critical: ALL tables/figures
    return [a for a in assets.tables if _table_row_count(a) <= NORMAL_TABLE_ROW_LIMIT]


def _assemble_sections(meta: tuple, chunks: list[Chunk], digests: list[dict],
                       reduce_out: dict, appended_assets: list[dict]) -> list[dict]:
    """Fixed-H2-order section tree; every prose unit carries its provenance location."""
    title = meta[0] or "this book"
    sections: list[dict] = []

    def subject_for(digest: dict) -> str:
        ents = digest.get("entities") or []
        if ents and isinstance(ents[0], dict) and ents[0].get("name"):
            return str(ents[0]["name"])
        return title

    # 1. What is *Title* about?
    kw = _dedup_keep_order([str(k) for d in digests for k in d.get("keywords", [])])[:10]
    body = str(reduce_out.get("executive_summary", "")).strip()
    if body and kw:
        body += "\n\n**Keywords:** " + ", ".join(kw)
    sections.append({"title": f"What is *{title}* about?", "llm": True,
                     "units": [_unit(_pronoun_lint(body, title), "overall")] if body else []})

    # 2. Core Thesis and Argument Map
    thesis = [str(t).strip() for t in reduce_out.get("thesis", []) if str(t).strip()]
    body = "\n".join(f"{i}. {t}" for i, t in enumerate(thesis, 1))
    sections.append({"title": "Core Thesis and Argument Map", "llm": True,
                     "units": [_unit(body, "overall")] if body else []})

    # 3. Key Facts and Figures (verbatim numbers from the map stage)
    facts = _dedup_keep_order([str(f) for d in digests for f in d.get("facts_numeric", [])])
    body = "\n".join(f"- {f}" for f in facts)
    sections.append({"title": "Key Facts and Figures", "llm": True,
                     "units": [_unit(body, "overall")] if body else []})

    # 4. Concept Glossary (canonical, merged, alphabetized)
    gloss = _glossary_entries(reduce_out, digests)
    body = "\n".join(f"- **{t}** — {d}" for t, d in gloss)
    sections.append({"title": "Concept Glossary", "llm": True,
                     "units": [_unit(body, "overall")] if body else []})

    # 5. Chapter/Section blocks, document order, one per chunk.
    for chunk, digest in zip(chunks, digests):
        loc = " > ".join(chunk.heading_path)
        ch = chunk.heading_path[-1]
        subject = subject_for(digest)
        units = []
        summary = str(digest.get("summary", "")).strip()
        if summary:
            s_body = _pronoun_lint(summary, subject)
            ch_kw = _dedup_keep_order([str(k) for k in digest.get("keywords", [])])
            if ch_kw and not digest.get("_failed"):
                s_body += "\n\n**Keywords:** " + ", ".join(ch_kw)
            units.append(_unit(s_body, loc, placeholder=bool(digest.get("_failed"))))
        claims = [str(c).strip() for c in digest.get("claims", []) if str(c).strip()]
        if claims:
            units.append(_unit("\n".join(f"- {c}" for c in claims), loc,
                               heading=f'What are the key claims of "{ch}"?',
                               heading_llm=False))   # deterministic heading: firewall skips it
        for qa in digest.get("qa", [])[:5]:
            if isinstance(qa, dict) and qa.get("q") and qa.get("a"):
                units.append(_unit(_pronoun_lint(str(qa["a"]).strip(), subject), loc,
                                   heading=_question_shape(str(qa["q"]))))
        sections.append({"title": ch, "llm": True, "units": units})

    # 6. Cross-Chapter Synthesis (question-shaped H3 per theme)
    units = []
    for th in reduce_out.get("themes", []):
        if not (isinstance(th, dict) and th.get("synthesis")):
            continue
        theme = str(th.get("theme", "")).strip()
        heading = theme if theme.endswith("?") else f"What does *{title}* say about {theme}?"
        t_body = _pronoun_lint(str(th["synthesis"]).strip(), title)
        t_kw = _dedup_keep_order([str(k) for k in th.get("keywords", [])])
        if t_kw:
            t_body += "\n\n**Keywords:** " + ", ".join(t_kw)
        units.append(_unit(t_body, "overall", heading=heading))
    sections.append({"title": "Cross-Chapter Synthesis", "llm": True, "units": units})

    # 7. Question Bank
    units = []
    for qa in reduce_out.get("question_bank", [])[:60]:
        if isinstance(qa, dict) and qa.get("q") and qa.get("a"):
            units.append(_unit(_pronoun_lint(str(qa["a"]).strip(), title), "overall",
                               heading=_question_shape(str(qa["q"]))))
    sections.append({"title": "Question Bank", "llm": True, "units": units})

    # 8. Entity Index
    ents = _entity_entries(reduce_out, digests)
    body = "\n".join(f"- **{e.get('name')}** ({e.get('kind', 'entity')}) — {e.get('one_liner', '')}"
                     for e in ents)
    sections.append({"title": "Entity Index", "llm": True,
                     "units": [_unit(body, "overall")] if body else []})

    # 9. Verbatim Tables and Figures — deterministic byte-for-byte copies.
    units = []
    for a in appended_assets:
        near = a["heading_path"][-1] if a["heading_path"] else (meta[0] or "document")
        label = "Figure" if a["kind"] == "figure" else "Table"
        units.append(_unit(a["raw"], near, heading=f'{label}: {_asset_preview(a)} (from "{near}")'))
    sections.append({"title": "Verbatim Tables and Figures (source-exact)",
                     "llm": False, "units": units})
    return sections


def _converter_version() -> str:
    try:
        from version import __version__
        return __version__
    except Exception:
        return "unknown"


def _render_document(meta: tuple, source_document: str, source_kind: str, models_str: str,
                     accuracy_critical: bool, source_tokens: int, sections: list[dict]) -> str:
    title, author, year = meta

    def q(s: str) -> str:
        return s.replace('"', '\\"')

    fm = ["---"]
    if title:
        fm.append(f'title: "{q(title)}"')
    if author:
        fm.append(f'author: "{q(author)}"')
    if year:
        fm.append(f"year: {year}")
    fm.append('document_type: "rag_distillate"')
    fm.append(f'source_document: "{q(source_document)}"')
    fm.append(f'source_kind: "{source_kind}"')
    fm.append(f'model: "{models_str}"')
    fm.append(f"accuracy_critical: {str(accuracy_critical).lower()}")
    fm.append(f"source_tokens_estimate: {source_tokens}")
    fm.append(f'converter_version: "{_converter_version()}"')
    fm.append(f'generated: "{time.strftime("%Y-%m-%d %H:%M:%S")}"')
    fm.append("---")

    about = (
        f"> **About this file:** Machine-distilled knowledge companion to *{title or source_document}* "
        f"by {author or 'an unknown author'},\n"
        "> optimized for retrieval and LLM context. Every section is self-contained. Tables are\n"
        f"> reproduced verbatim from the source, never LLM-generated. Full text: `{source_document}`."
    )

    parts = ["\n".join(fm), about]
    for sec in sections:
        parts.append(f"## {sec['title']}")
        live = [u for u in sec["units"] if not u.get("dropped") and u["body"].strip()]
        if not live:
            parts.append("_None found in this document._")
            continue
        for u in live:
            if u.get("heading"):
                parts.append(f"### {u['heading']}")
            if sec["llm"]:
                parts.append(_enforce_block(u["body"], _footer(meta, u["loc"])))
            else:
                parts.append(u["body"])    # verbatim appendix: byte-exact, no reflow
    return "\n\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
# Verification (deterministic, always runs)
# --------------------------------------------------------------------------- #

def _scan_numerals_for_firewall(text: str) -> set[str]:
    return extract_numerals(_ORDINAL_RE.sub("", text))     # list ordinals exempt


def _is_exempt(n: str, assets: VerbatimAssets) -> bool:
    try:
        v = float(n)
    except ValueError:
        return False
    if v == int(v) and 0 <= int(v) <= 12:                  # small integers exempt
        return True
    # 4-digit years are exempt when present anywhere in the source text — checked
    # against the dedicated year scan (years_source), so a year the numeral pass
    # folded into a larger figure (e.g. "Model 3.2009") is still rescued.
    return bool(re.fullmatch(r"[12]\d{3}", n)) and n in assets.years_source


def _flag_unit_text(text: str, assets: VerbatimAssets) -> list[str]:
    return sorted(n for n in _scan_numerals_for_firewall(text)
                  if n not in assets.numerals_source and not _is_exempt(n, assets))


def _unit_scan_text(unit: dict) -> str:
    """LLM-authored text of a unit: the body, plus the heading only when the
    heading is LLM text — deterministic chunk-label headings (e.g. 'What are the
    key claims of "Pages ~120–135"?') must never feed the firewall."""
    head = (unit.get("heading") or "") if unit.get("heading_llm", True) else ""
    return head + "\n" + unit["body"]


def _drop_unit(unit: dict, report: VerificationReport) -> None:
    unit["dropped"] = True
    report.dropped_items += 1


def _apply_numeral_firewall(sections: list[dict], assets: VerbatimAssets, *,
                            accuracy_critical: bool, report: VerificationReport,
                            log: Callable[[str], None]) -> bool:
    """Hallucinated-number scan over LLM text. Returns False when an unverified
    number would survive an accuracy-critical companion (=> abort, no file)."""
    for sec in sections:
        if not sec["llm"]:
            continue
        for unit in sec["units"]:
            if unit.get("placeholder"):    # deterministic self-generated text, not LLM output
                continue
            flagged = _flag_unit_text(_unit_scan_text(unit), assets)
            if not flagged:
                continue
            report.flagged_numbers.extend(flagged)
            if accuracy_critical:
                log(f"RAG distill: dropping block with unverified figure(s): {', '.join(flagged)}")
                _drop_unit(unit, report)
            else:
                unit["body"] += "\n\n⚠ unverified figure: " + ", ".join(flagged)
                log(f"RAG distill: unverified figure(s) {', '.join(flagged)} — annotated in companion")
    report.flagged_numbers = sorted(set(report.flagged_numbers))
    if accuracy_critical:
        for sec in sections:               # belt and suspenders: nothing flagged may survive
            if not sec["llm"]:
                continue
            for unit in sec["units"]:
                if unit.get("dropped") or unit.get("placeholder"):
                    continue
                if _flag_unit_text(_unit_scan_text(unit), assets):
                    return False
    return True


def _verify_table_survival(doc: str, appended_assets: list[dict],
                           report: VerificationReport, log: Callable[[str], None]) -> str:
    """Every appended-table numeral must appear in the companion; re-append on miss."""
    total: set[str] = set()
    for a in appended_assets:
        total |= extract_numerals(a["raw"])
    report.table_numerals_total = len(total)
    doc_numerals = extract_numerals(doc)
    missing = {n for n in total if n not in doc_numerals}
    if missing:
        for a in appended_assets:
            if extract_numerals(a["raw"]) & missing:
                near = a["heading_path"][-1] if a["heading_path"] else "document"
                doc += (f"\n\n### Table: {_asset_preview(a)} (from \"{near}\") — "
                        f"re-appended by verification repair\n\n{a['raw']}\n")
                log(f"RAG distill: verification repair — re-appended table from '{near}' "
                    "(numerals were missing from the companion)")
        doc_numerals = extract_numerals(doc)
        missing = {n for n in total if n not in doc_numerals}
    report.table_numerals_present = len(total) - len(missing)
    return doc


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

def _default_out_path(md_path: str) -> str:
    return str(Path(md_path).with_suffix("")) + COMPANION_SUFFIX


def distill_markdown(
    md_path: str,
    *,
    quality: str = "standard",                # key into QUALITY_MODELS
    accuracy_critical: bool = False,
    cost_cap_usd: float = DEFAULT_COST_CAP_USD,
    out_path: str | None = None,              # default: md_path stem + COMPANION_SUFFIX
    source_kind: str = "epub",                # "epub" | "pdf"
    log: Callable[[str], None] = print,
    status: dict | None = None,               # live GUI fields: chunk, chunks_total, cost_usd
    dry_run: bool = False,                    # plan + estimate only; zero network, no SDK import
    client_factory: Callable[[], Any] | None = None,   # TEST SEAM: returns genai.Client-like
) -> DistillResult:
    """Run the full distillation pipeline. Catches everything; never raises."""
    usage = UsageTotals()
    holder: dict = {"key": None}

    def _log(msg: str) -> None:
        try:
            log(_scrub(str(msg), holder["key"]))
        except Exception:
            pass

    try:
        return _distill(md_path, quality, accuracy_critical, cost_cap_usd, out_path,
                        source_kind, _log, status, dry_run, client_factory, usage, holder)
    except Exception as e:
        err = _scrub(f"{type(e).__name__}: {e}", holder["key"]).replace("\n", " ")
        _log(f"RAG distill error: {err}")
        return DistillResult(ok=False, companion_path=None, usage=usage, error=err)


def _distill(md_path: str, quality: str, accuracy_critical: bool, cost_cap_usd: float,
             out_path: str | None, source_kind: str, _log: Callable[[str], None],
             status: dict | None, dry_run: bool,
             client_factory: Callable[[], Any] | None,
             usage: UsageTotals, holder: dict) -> DistillResult:
    md_path = str(md_path)
    if md_path.endswith(COMPANION_SUFFIX):   # a companion is never a distillation source
        _log(f"RAG distill: skipping {Path(md_path).name} — .rag.md companions are never re-distilled")
        return DistillResult(ok=False, companion_path=None, usage=usage,
                             error="companion .rag.md files are not distillation sources")
    if quality not in QUALITY_MODELS:
        quality = "standard"
    map_model, reduce_model = QUALITY_MODELS[quality]

    # ---- Stage 0: preflight (deterministic, free) ----
    if not dry_run:                          # dry_run needs neither the SDK nor a key
        if not is_available():
            _log("RAG distill unavailable: pip install 'epub2md[rag]'")
            return DistillResult(False, None, usage, skipped_reason="sdk_missing")
        key = resolve_api_key(log=_log)
        if not key:
            _log("RAG distill skipped: set GEMINI_API_KEY or create "
                 "~/.epub2md_gemini_key (chmod 600)")
            return DistillResult(False, None, usage, skipped_reason="no_api_key")
        holder["key"] = key

    md_text = Path(md_path).read_text(encoding="utf-8")
    meta = _extract_metadata(md_text, md_path)
    body, _ = _strip_frontmatter(md_text)
    assets = extract_verbatim_assets(md_text)
    chunk_input = _excise_verbatim_regions(body) if accuracy_critical else body
    # body is already frontmatter-stripped: strip exactly once (a second strip
    # would eat the first content page of a PDF whose first page is image-only).
    chunks = plan_chunks(chunk_input, source_type=source_kind, strip_frontmatter=False)
    est = _estimate_from_chunks(chunks, quality)
    est_str = "cost unknown" if est["est_cost_usd"] is None else f"${est['est_cost_usd']:.2f}"
    _log(f"RAG distill: ~{est['chunks']} chunks, est. {est_str} (cap ${cost_cap_usd:.2f})")
    if status is not None:
        status["chunk"] = 0
        status["chunks_total"] = len(chunks)

    if dry_run:
        for c in chunks:
            _log(f"  chunk {c.index + 1}: {' > '.join(c.heading_path)} (~{c.token_estimate:,} tok)")
        if assets.tables:
            _log(f"  verbatim assets: {len(assets.tables)} table(s)/figure(s), "
                 f"{len(assets.numerals_tables)} table numeral(s)")
        _log("RAG distill dry-run: no API calls made, no companion written.")
        return DistillResult(True, None, usage, chunks_total=len(chunks))

    if est["est_cost_usd"] is not None and est["est_cost_usd"] > cost_cap_usd:
        _log(f"RAG distill skipped: estimated {est_str} exceeds the ${cost_cap_usd:.2f}/file cap — "
             "raise rag_distill_cost_cap_usd in ~/.epub2md_preferences.json to proceed")
        return DistillResult(False, None, usage, skipped_reason="cost_cap", chunks_total=len(chunks))

    if not chunks:
        _log("RAG distill skipped: no distillable content found")
        return DistillResult(False, None, usage, error="no distillable content")

    file_name = Path(md_path).name
    models = [map_model, reduce_model]

    def record(outcome: str, failed: int) -> dict:
        try:
            return record_run(usage, source_kind=source_kind, file_name=file_name,
                              quality=quality, models=models, accuracy_critical=accuracy_critical,
                              outcome=outcome, chunks=len(chunks), chunks_failed=failed)
        except Exception as e:               # ledger failure is never fatal
            _log(f"RAG distill: could not update usage ledger ({e})")
            return {}

    typed = client_factory is None           # injected test clients never touch the SDK
    try:
        client = client_factory() if client_factory else _get_client(holder["key"])
    except Exception as e:
        _log(f"RAG distill: could not create Gemini client ({e})")
        return DistillResult(False, None, usage, skipped_reason="api_error",
                             error=_scrub(str(e), holder["key"]))

    # ---- Stage 1: MAP ---- / ---- Stage 2: REDUCE ----
    map_config = _make_config(map_model, thinking="low",
                              max_output_tokens=MAP_MAX_OUTPUT_TOKENS, typed=typed)
    digests: list[dict] = []
    chunks_failed = 0
    try:
        for i, chunk in enumerate(chunks):
            if status is not None:
                status["chunk"] = i + 1
                status["chunks_total"] = len(chunks)
            try:
                digest = _map_chunk(client, map_model, chunk, meta, map_config, usage,
                                    status, _log, accuracy_critical, cost_cap_usd)
            except _CostCapExceeded:
                raise
            except _NonRetryableAPIError as e:
                # A dead key/permission error fails every chunk identically —
                # abort the whole run now instead of grinding through the rest.
                chunks_failed += 1
                _log(f"RAG distill aborted: {e} — retrying cannot fix this; no companion written")
                record("failed", chunks_failed)
                return DistillResult(False, None, usage, skipped_reason="api_error",
                                     chunks_total=len(chunks), chunks_failed=chunks_failed,
                                     error=_scrub(str(e), holder["key"]))
            except Exception as e:
                chunks_failed += 1
                _log(f"RAG distill: chunk {i + 1}/{len(chunks)} failed — noted in companion ({e})")
                if accuracy_critical:        # AC mode does not tolerate any failed chunk
                    _log("RAG distill aborted: accuracy-critical mode allows no failed chunks")
                    record("failed", chunks_failed)
                    return DistillResult(False, None, usage, skipped_reason="too_many_failures",
                                         chunks_total=len(chunks), chunks_failed=chunks_failed)
                # chunks_failed is monotonic: once the ratio is crossed the abort
                # is inevitable — take it now, not after every remaining chunk.
                if chunks_failed > FAILURE_ABORT_RATIO * len(chunks):
                    _log(f"RAG distill aborted: {chunks_failed}/{len(chunks)} chunks failed (>30%) — "
                         "no companion written")
                    record("failed", chunks_failed)
                    return DistillResult(False, None, usage, skipped_reason="too_many_failures",
                                         chunks_total=len(chunks), chunks_failed=chunks_failed)
                digest = _placeholder_digest(chunk)
            digests.append(digest)

        try:
            reduce_out = _run_reduce(client, quality, [d for d in digests if not d.get("_failed")],
                                     meta, usage, status, _log, cost_cap_usd, typed)
        except _CostCapExceeded:
            raise
        except Exception as e:
            _log(f"RAG distill: reduce stage failed ({e}) — no companion written")
            record("failed", chunks_failed)
            return DistillResult(False, None, usage, skipped_reason="api_error",
                                 chunks_total=len(chunks), chunks_failed=chunks_failed,
                                 error=_scrub(str(e), holder["key"]))
    except _CostCapExceeded as e:
        _log(f"RAG distill aborted mid-run: {e} — spend recorded, no companion written")
        record("aborted", chunks_failed)
        return DistillResult(False, None, usage, skipped_reason="cost_cap_midrun",
                             chunks_total=len(chunks), chunks_failed=chunks_failed)

    # ---- Stage 3: ASSEMBLE (deterministic) ----
    report = VerificationReport()
    appended = _select_appendix_assets(assets, accuracy_critical)
    sections = _assemble_sections(meta, chunks, digests, reduce_out, appended)

    # ---- Stage 4: VERIFY (deterministic, always runs) ----
    if not _apply_numeral_firewall(sections, assets, accuracy_critical=accuracy_critical,
                                   report=report, log=_log):
        _log("RAG distill aborted: unverified figures could not be removed — "
             "no companion written (verification failed)")
        record("failed", chunks_failed)
        return DistillResult(False, None, usage, skipped_reason="verification_failed",
                             verification=report, chunks_total=len(chunks),
                             chunks_failed=chunks_failed)

    doc = _render_document(meta, file_name, source_kind, f"{map_model} + {reduce_model}",
                           accuracy_critical, estimate_tokens(md_text), sections)
    doc = _verify_table_survival(doc, appended, report, _log)

    # ---- Stage 5: WRITE (atomic; abort paths never leave a partial file) ----
    target = out_path or _default_out_path(md_path)
    try:
        _atomic_write_text(Path(target), doc)
    except Exception as e:
        _log(f"RAG distill: could not write companion ({e})")
        record("failed", chunks_failed)
        return DistillResult(False, None, usage, error=_scrub(str(e), holder["key"]),
                             verification=report, chunks_total=len(chunks),
                             chunks_failed=chunks_failed)

    # ---- Stage 6: LEDGER + REPORT ----
    lifetime = record("partial" if chunks_failed else "success", chunks_failed)
    _log(f"RAG distill: wrote {Path(target).name}")
    _log(format_usage_line(usage, lifetime))
    return DistillResult(True, str(target), usage, verification=report,
                         chunks_total=len(chunks), chunks_failed=chunks_failed)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Distill a converted Markdown file into a RAG-optimized "
                    ".rag.md companion (Gemini API).")
    parser.add_argument("markdown", nargs="?", help="Path to the converted .md file")
    parser.add_argument("--quality", choices=sorted(QUALITY_MODELS), default="standard")
    parser.add_argument("--accuracy-critical", action="store_true",
                        help="Verbatim tables + numeral verification; abort over deliver-wrong")
    parser.add_argument("--cost-cap", type=float, default=DEFAULT_COST_CAP_USD, metavar="USD")
    parser.add_argument("-o", "--output", help=f"Companion path (default: <stem>{COMPANION_SUFFIX})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the chunk plan and cost estimate; zero API calls")
    parser.add_argument("--usage", action="store_true", help="Print usage-ledger totals and exit")
    args = parser.parse_args()

    if args.usage:
        ledger = load_usage_ledger()
        print(json.dumps({"lifetime": ledger.get("lifetime", {}),
                          "recent_runs": ledger.get("runs", [])[-10:]}, indent=2))
        sys.exit(0)
    if not args.markdown:
        parser.error("markdown file required (or use --usage)")

    result = distill_markdown(args.markdown, quality=args.quality,
                              accuracy_critical=args.accuracy_critical,
                              cost_cap_usd=args.cost_cap, out_path=args.output,
                              dry_run=args.dry_run)
    if result.ok:
        sys.exit(0)
    sys.exit(2 if result.skipped_reason else 1)


if __name__ == "__main__":
    main()
