"""GUI status-lifecycle tests for the RAG-distill / self-improvement panels.

Covers the server-side invariants the frontend pollers rely on:

* /convert resets the post-step status dicts synchronously, so a poller can
  never observe a previous run's completed state (stale cost/results).
* The live cost badge fields accumulate run-level totals across a multi-file
  batch (per-file writes from distill_markdown must not clobber run totals).
* A run that converts zero files still finalizes the panels (clean
  "nothing to distill/evaluate" terminal state — no forever-spinner).
* The engine sentinel 'none' is served verbatim by /self_improve_status
  (the JS maps it to "Judge: skipped (no engine)").
* Per-source status dicts + extended running guards keep overlapping
  EPUB/PDF runs from corrupting each other's status.

Hermetic by construction: process_folder, rag_distill, and self_improve are
all stubbed; no pandoc, no network, no real conversions.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = REPO_ROOT / "templates" / "index.html"


@pytest.fixture
def gui_mod(tmp_path, monkeypatch):
    gui = pytest.importorskip("gui")
    monkeypatch.setattr(gui, "PREFERENCES_FILE", str(tmp_path / "prefs.json"))
    return gui


def _pending_rag(source):
    """Not-yet-run RAG status shape (kept inline so pre-fix gui still loads)."""
    return {"running": False, "progress": [], "processed": 0, "total": 0,
            "chunk": 0, "chunks_total": 0, "calls": 0,
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
            "estimate_only": False, "lifetime_usd": None,
            "source": source, "completed": False}


def _pending_si():
    return {"running": False, "progress": [], "evaluated": 0, "total": 0,
            "issues_filed": 0, "engine": None, "completed": False}


@pytest.fixture(autouse=True)
def _fresh_status(gui_mod, monkeypatch):
    """Start every test from pristine status globals (and restore after)."""
    monkeypatch.setattr(gui_mod, "conversion_status",
                        {"running": False, "progress": [], "current": 0,
                         "total": 0, "completed": False})
    monkeypatch.setattr(gui_mod, "pdf_conversion_status",
                        {"running": False, "progress": [], "completed": False,
                         "success": False, "output_file": None, "error": None})
    monkeypatch.setattr(gui_mod, "rag_distill_status", _pending_rag("epub"))
    monkeypatch.setattr(gui_mod, "rag_distill_status_pdf", _pending_rag("pdf"), raising=False)
    monkeypatch.setattr(gui_mod, "self_improvement_status", _pending_si())


def _wait(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _stale_rag(gui, **overrides):
    st = _pending_rag("epub")
    st.update({"completed": True, "calls": 9, "cost_usd": 9.99,
               "processed": 3, "total": 3, "progress": ["old run line"]})
    st.update(overrides)
    return st


class FakeUsage:
    """Stands in for rag_distill.UsageTotals (same field names/defaults)."""

    def __init__(self, calls=0, input_tokens=0, output_tokens=0,
                 thought_tokens=0, cost_usd=0.0, estimate_only=False):
        self.calls = calls
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.thought_tokens = thought_tokens
        self.cost_usd = cost_usd
        self.estimate_only = estimate_only


def _fake_rag_module(distill_fn):
    mod = ModuleType("rag_distill")
    mod.UsageTotals = FakeUsage
    mod.distill_markdown = distill_fn
    # No load_usage_ledger/format_usage_line: the runner's summary try/except
    # must fall back to the generic "N/M file(s) distilled" line.
    return mod


# --------------------------------------------------------------------------- #
# Status reset on conversion start (stale previous-run state must vanish)
# --------------------------------------------------------------------------- #

def test_convert_resets_poststep_status_before_thread_runs(gui_mod, tmp_path, monkeypatch):
    gui = gui_mod
    client = gui.app.test_client()

    # Previous run left BOTH panels terminally completed with real results.
    monkeypatch.setattr(gui, "rag_distill_status", _stale_rag(gui))
    si = _pending_si()
    si.update({"completed": True, "issues_filed": 7, "engine": "api", "progress": ["old"]})
    monkeypatch.setattr(gui, "self_improvement_status", si)

    epub = tmp_path / "book.epub"
    epub.write_bytes(b"fake epub bytes")

    started, release = threading.Event(), threading.Event()

    def fake_process_folder(work_dir, output_folder):
        started.set()
        release.wait(10)
        return []

    monkeypatch.setattr(gui, "process_folder", fake_process_folder)

    resp = client.post("/convert", json={"items": [{"kind": "file", "path": str(epub)}]})
    assert resp.status_code == 200
    try:
        # The reset happens in the route, before completed=True is reachable:
        # the pollers can only ever see THIS run's pending state, never the
        # previous run's cost/results.
        rag = client.get("/rag_distill_status").get_json()
        assert rag["completed"] is False
        assert rag["cost_usd"] == 0.0 and rag["calls"] == 0
        assert rag["progress"] == []
        sis = client.get("/self_improve_status").get_json()
        assert sis["completed"] is False
        assert sis["issues_filed"] == 0 and sis["progress"] == []
    finally:
        release.set()
    assert started.wait(5)
    assert _wait(lambda: not gui.conversion_status["running"])


# --------------------------------------------------------------------------- #
# Zero-pairs run: panels finalize as a clean "nothing to distill" state
# --------------------------------------------------------------------------- #

def test_zero_pairs_run_finalizes_panels(gui_mod, tmp_path, monkeypatch):
    gui = gui_mod
    client = gui.app.test_client()
    client.post("/save_preferences", json={"rag_distill_enabled": True,
                                           "self_improvement_enabled": True})

    # Stale completed state from an earlier distill must not resurface either.
    monkeypatch.setattr(gui, "rag_distill_status", _stale_rag(gui))

    epub = tmp_path / "book.epub"
    epub.write_bytes(b"fake epub bytes")
    monkeypatch.setattr(gui, "process_folder", lambda *a, **k: [])  # every file failed

    resp = client.post("/convert", json={"items": [{"kind": "file", "path": str(epub)}]})
    assert resp.status_code == 200
    assert _wait(lambda: gui.conversion_status["completed"] and not gui.conversion_status["running"])

    rag = client.get("/rag_distill_status").get_json()
    assert rag["completed"] is True and rag["running"] is False
    assert rag["total"] == 0 and rag["processed"] == 0
    assert rag["calls"] == 0 and rag["cost_usd"] == 0.0          # not the stale $9.99
    assert any("nothing to distill" in line.lower() for line in rag["progress"])

    sis = client.get("/self_improve_status").get_json()
    assert sis["completed"] is True and sis["total"] == 0
    assert any("nothing to evaluate" in line.lower() for line in sis["progress"])


# --------------------------------------------------------------------------- #
# Run-total accumulation across a 2-file batch (live badge = run-cumulative)
# --------------------------------------------------------------------------- #

def test_run_totals_accumulate_across_files(gui_mod, monkeypatch):
    gui = gui_mod
    snapshots = []

    def fake_distill(md_path, **kwargs):
        status = kwargs["status"]
        for call_idx in (1, 2):
            # Mimic rag_distill._accumulate_usage: OVERWRITE the passed dict
            # with this FILE's cumulative usage after every API call.
            status["calls"] = call_idx
            status["input_tokens"] = 100 * call_idx
            status["output_tokens"] = 50 * call_idx
            status["cost_usd"] = 0.05 * call_idx
            status["estimate_only"] = False
            snapshots.append({"calls": gui.rag_distill_status["calls"],
                              "cost_usd": gui.rag_distill_status["cost_usd"]})
        return SimpleNamespace(ok=True, usage=FakeUsage(
            calls=2, input_tokens=200, output_tokens=100, cost_usd=0.10))

    monkeypatch.setitem(sys.modules, "rag_distill", _fake_rag_module(fake_distill))
    gui._run_rag_distill([("a.epub", "a.md"), ("b.epub", "b.md")], {},
                         source="epub", accuracy_critical=False)

    # File 2's first call: the badge must show run-cumulative (2 prior calls,
    # $0.10 prior spend, + this call) — not reset to the per-file figure.
    assert snapshots[2]["calls"] == 3
    assert snapshots[2]["cost_usd"] == pytest.approx(0.15)
    assert snapshots[3]["calls"] == 4
    assert snapshots[3]["cost_usd"] == pytest.approx(0.20)

    st = gui.rag_distill_status
    assert st["completed"] is True and st["running"] is False
    assert st["processed"] == 2 and st["total"] == 2
    assert st["calls"] == 4
    assert st["cost_usd"] == pytest.approx(0.20)
    assert st["input_tokens"] == 400 and st["output_tokens"] == 200
    assert any("2/2" in line for line in st["progress"])         # fallback summary


def test_estimate_only_sticks_across_files(gui_mod, monkeypatch):
    """An unpriced file 1 must keep the run flagged estimate_only during file 2."""
    gui = gui_mod
    mid_run_flags = []
    files = iter([FakeUsage(calls=1, cost_usd=None, estimate_only=True),
                  FakeUsage(calls=1, cost_usd=0.05)])

    def fake_distill(md_path, **kwargs):
        status = kwargs["status"]
        usage = next(files)
        status["calls"] = 1
        status["cost_usd"] = usage.cost_usd if usage.cost_usd is not None else 0.0
        status["estimate_only"] = usage.estimate_only
        mid_run_flags.append(gui.rag_distill_status["estimate_only"])
        return SimpleNamespace(ok=True, usage=usage)

    monkeypatch.setitem(sys.modules, "rag_distill", _fake_rag_module(fake_distill))
    gui._run_rag_distill([("a.epub", "a.md"), ("b.epub", "b.md")], {},
                         source="epub", accuracy_critical=False)

    assert mid_run_flags == [True, True]                          # no mid-run flip to False
    assert gui.rag_distill_status["estimate_only"] is True


# --------------------------------------------------------------------------- #
# Engine 'none' sentinel is served verbatim (JS maps it to "skipped")
# --------------------------------------------------------------------------- #

def test_engine_none_serialized_verbatim(gui_mod, monkeypatch):
    gui = gui_mod
    stub = ModuleType("self_improve")
    stub.evaluate_conversion = lambda *a, **k: {
        "status": "skipped", "reason": "no_judge_engine", "engine": "none", "filed": 0}
    monkeypatch.setitem(sys.modules, "self_improve", stub)

    gui._run_self_improvement([("a.epub", "a.md")], None)

    data = gui.app.test_client().get("/self_improve_status").get_json()
    assert data["engine"] == "none"          # exact sentinel — JS keys off it
    assert data["completed"] is True and data["evaluated"] == 1
    assert data["issues_filed"] == 0


# --------------------------------------------------------------------------- #
# Per-source status: EPUB and PDF runs cannot clobber each other
# --------------------------------------------------------------------------- #

def test_rag_status_is_per_source(gui_mod, monkeypatch):
    gui = gui_mod
    client = gui.app.test_client()

    # Marker state on the EPUB side that a PDF run must leave untouched.
    monkeypatch.setattr(gui, "rag_distill_status", _stale_rag(gui, cost_usd=1.23))

    def fake_distill(md_path, **kwargs):
        kwargs["status"]["calls"] = 1
        kwargs["status"]["cost_usd"] = 0.07
        return SimpleNamespace(ok=True, usage=FakeUsage(calls=1, cost_usd=0.07))

    monkeypatch.setitem(sys.modules, "rag_distill", _fake_rag_module(fake_distill))
    gui._run_rag_distill([("doc.pdf", "doc.md")], {}, source="pdf", accuracy_critical=False)

    pdf = client.get("/rag_distill_status?source=pdf").get_json()
    assert pdf["source"] == "pdf" and pdf["completed"] is True
    assert pdf["cost_usd"] == pytest.approx(0.07) and pdf["processed"] == 1

    epub = client.get("/rag_distill_status").get_json()          # default: epub
    assert epub["source"] == "epub"
    assert epub["cost_usd"] == pytest.approx(1.23)               # marker survived


# --------------------------------------------------------------------------- #
# Overlap guards: the 409 covers the whole run including post-steps
# --------------------------------------------------------------------------- #

def test_convert_409_while_poststep_running(gui_mod, tmp_path, monkeypatch):
    gui = gui_mod
    client = gui.app.test_client()
    client.post("/save_preferences", json={"rag_distill_enabled": True})

    epub = tmp_path / "book.epub"
    epub.write_bytes(b"fake epub bytes")
    monkeypatch.setattr(gui, "process_folder",
                        lambda *a, **k: [(str(epub), str(tmp_path / "book.md"))])

    distilling, hold = threading.Event(), threading.Event()

    def fake_distill(md_path, **kwargs):
        distilling.set()
        hold.wait(10)
        return SimpleNamespace(ok=True, usage=FakeUsage(calls=1, cost_usd=0.01))

    monkeypatch.setitem(sys.modules, "rag_distill", _fake_rag_module(fake_distill))

    resp = client.post("/convert", json={"items": [{"kind": "file", "path": str(epub)}]})
    assert resp.status_code == 200
    try:
        assert distilling.wait(5)
        # Conversion is 'completed' but the distill post-step is still writing
        # to the status globals: a second run must be refused, not admitted.
        assert gui.conversion_status["completed"] is True
        resp2 = client.post("/convert", json={"items": [{"kind": "file", "path": str(epub)}]})
        assert resp2.status_code == 409
    finally:
        hold.set()
    assert _wait(lambda: not gui.conversion_status["running"])
    assert gui.rag_distill_status["completed"] is True


def test_convert_pdf_409_while_running(gui_mod, monkeypatch):
    gui = gui_mod
    monkeypatch.setattr(gui, "PDF_CONVERTER_AVAILABLE", True)
    gui.pdf_conversion_status["running"] = True                  # mid-run (incl. post-steps)
    resp = gui.app.test_client().post("/convert_pdf", json={})
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# Template contract tripwires for the JS-side fixes (not unit-testable as JS)
# --------------------------------------------------------------------------- #

def test_template_js_contracts():
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    # Engine 'none' sentinel maps to a "skipped" label, not the API label.
    assert "data.engine === 'none'" in html
    assert "skipped (no engine)" in html
    # Poller fetches the per-source status endpoint.
    assert "/rag_distill_status?source=" in html
    # Zero-work completion renders as a clean idle state, not a spinner.
    assert "Nothing to distill" in html
    # Completion re-renders the main log so Copy Logs captures the cost line.
    assert "async function refreshMainLog" in html
    assert "refreshMainLog(suffix)" in html
