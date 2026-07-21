"""Unit tests for the judge orchestration, dedup, caps, and circuit breaker.

No network, no `gh`, and no real `claude` CLI: the Anthropic client and
``subprocess.run`` are monkeypatched and all filing runs in dry-run mode (which
never shells out).
"""

import json as _json
import subprocess
import sys

import pytest

import self_improve as si
from epub_to_md_converter import collect_quality_signals
from self_improve import Finding, JudgeReport


def mk(severity="major", category="heading_structure", title="Recover missing headings",
       systemic=True, confidence=0.85):
    return Finding(severity=severity, category=category, title=title, evidence="e",
                   suggested_fix="f", is_systemic=systemic, confidence=confidence)


def _envelope(payload=None, subtype="success", is_error=False, result_str=None):
    """Build a `claude -p --output-format json` result envelope (CLI 2.1.216 shape)."""
    env = {"type": "result", "subtype": subtype, "is_error": is_error,
           "result": result_str if result_str is not None else _json.dumps(payload),
           "stop_reason": "tool_use", "num_turns": 2, "usage": {}}
    if payload is not None:
        env["structured_output"] = payload
    return _json.dumps(env)


_CLEAN_REPORT = {"overall_assessment": "faithful", "conversion_is_acceptable": True, "findings": []}


def _force_cli_engine(monkeypatch):
    """No keys, no override, `claude` on PATH -> _select_engine() returns 'cli'."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("EPUB2MD_JUDGE_ENGINE", raising=False)
    monkeypatch.setattr(si.shutil, "which", lambda *_: "/usr/local/bin/claude")


def _redirect_history(monkeypatch, tmp_path):
    """Point the ledger at a tmp file (save/load defaults were bound at def time)."""
    hist = tmp_path / "eval_history.json"
    orig_load, orig_save = si.load_history, si.save_history
    monkeypatch.setattr(si, "HISTORY_PATH", hist)
    monkeypatch.setattr(si, "load_history", lambda path=hist: orig_load(path))
    monkeypatch.setattr(si, "save_history", lambda history, path=hist: orig_save(history, path))
    return hist


# --------------------------------------------------------------------------- #
# Signature + filtering + merge
# --------------------------------------------------------------------------- #

def test_signature_is_book_independent_but_category_sensitive():
    a = mk(title="Recover headings for Chapter 3")
    b = mk(title="Recover headings for Chapter 17")
    c = mk(title="Recover headings for Chapter 3", category="artifact_noise")
    assert si.signature(a) == si.signature(b)      # same defect, different book/chapter
    assert si.signature(a) != si.signature(c)      # different category


def test_filtering_rules():
    assert si._keep(mk(severity="major", confidence=0.7))
    assert not si._keep(mk(severity="major", confidence=0.4))        # below floor
    assert not si._keep(mk(severity="nit", systemic=False))         # cosmetic, not systemic
    assert si._keep(mk(severity="nit", systemic=True))              # systemic nit kept
    assert not si._keep(mk(severity="bogus"))                       # invalid severity


def test_merge_dedups_keeping_highest_confidence():
    r1 = JudgeReport(overall_assessment="", conversion_is_acceptable=False,
                     findings=[mk(title="Recover headings for Chapter 1", confidence=0.7)])
    r2 = JudgeReport(overall_assessment="", conversion_is_acceptable=False,
                     findings=[mk(title="Recover headings for Chapter 9", confidence=0.95)])
    merged = si.merge_findings([r1, r2])
    assert len(merged) == 1
    assert merged[0].confidence == 0.95


# --------------------------------------------------------------------------- #
# Filing: caps, dedup, escalation, circuit breaker (all dry-run)
# --------------------------------------------------------------------------- #

def _signals():
    return {"optimization_score": 90.0, "heading_count": 0, "artifacts": {}}


def test_per_run_cap():
    findings = [mk(category="formatting", title=f"Fix distinct problem alpha {i}") for i in range(5)]
    # Make them distinct signatures via different category words.
    findings = [
        mk(category="missing_content", title="Restore dropped preface"),
        mk(category="heading_structure", title="Recover chapter headings"),
        mk(category="artifact_noise", title="Strip leftover html blocks"),
        mk(category="ordering", title="Reorder shuffled chapters"),
        mk(category="encoding", title="Fix mojibake in body"),
    ]
    history = si._default_history()
    outcomes = si.file_findings(findings, _signals(), "Book", history, dry_run=True, logger=lambda *a: None)
    actions = [o["action"] for o in outcomes]
    assert actions.count("dry_run") == si.MAX_ISSUES_PER_RUN
    assert actions.count("capped_run") == len(findings) - si.MAX_ISSUES_PER_RUN


def test_dedup_against_ledger():
    finding = mk(title="Recover chapter headings")
    sig = si.signature(finding)
    history = si._default_history()
    history["ledger"][sig] = {"issue_number": 42, "state": "open", "label": si.ISSUE_LABEL,
                              "first_seen": "2026-01-01", "last_seen": "2026-01-01", "occurrences": 0}
    outcomes = si.file_findings([finding], _signals(), "Book", history, dry_run=True, logger=lambda *a: None)
    assert outcomes[0]["action"] in ("deduped", "escalated_hold")
    assert history["ledger"][sig]["occurrences"] == 1  # bumped, not refiled


def test_occurrence_escalation_to_hold():
    finding = mk(title="Recover chapter headings")
    sig = si.signature(finding)
    history = si._default_history()
    history["ledger"][sig] = {"issue_number": 42, "state": "open", "label": si.ISSUE_LABEL,
                              "first_seen": "2026-01-01", "last_seen": "2026-01-01",
                              "occurrences": si.MAX_OCCURRENCES - 1}
    outcomes = si.file_findings([finding], _signals(), "Book", history, dry_run=True, logger=lambda *a: None)
    assert outcomes[0]["action"] == "escalated_hold"


def test_circuit_breaker_routes_to_hold_label():
    finding = mk(title="Recover chapter headings")
    history = si._default_history()
    history["circuit_breaker"]["auto_merge_disabled"] = True
    si.file_findings([finding], _signals(), "Book", history, dry_run=True, logger=lambda *a: None)
    sig = si.signature(finding)
    assert history["ledger"][sig]["label"] == si.HOLD_LABEL


# --------------------------------------------------------------------------- #
# Orchestrator: skip without any engine; full run with a mocked client
# --------------------------------------------------------------------------- #

def test_evaluate_skips_without_api_key(monkeypatch, synthetic_epub):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("EPUB2MD_JUDGE_ENGINE", raising=False)
    monkeypatch.setattr(si.shutil, "which", lambda *_: None)  # dev machines have `claude` on PATH
    epub, md = synthetic_epub
    result = si.evaluate_conversion(epub, md, logger=lambda *a: None)
    assert result["status"] == "skipped" and result["reason"] == "no_judge_engine"


def test_run_judge_with_mocked_client(monkeypatch, synthetic_epub):
    import anthropic

    sample_report = JudgeReport(
        overall_assessment="looks fine", conversion_is_acceptable=True, findings=[]
    )

    class _Msgs:
        def parse(self, **kwargs):
            return type("R", (), {"parsed_output": sample_report})()

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Msgs()

    monkeypatch.setattr(anthropic, "Anthropic", _Client)
    epub, md = synthetic_epub
    signals = collect_quality_signals(epub, md)
    reports = si.run_judge(epub, md, signals, si.DEFAULT_MODEL, engine="api", logger=lambda *a: None)
    assert reports and isinstance(reports[0], JudgeReport)
    assert si.merge_findings(reports) == []  # acceptable conversion -> nothing to file


# --------------------------------------------------------------------------- #
# Engine selection + the claude-CLI judge engine (subprocess fully mocked)
# --------------------------------------------------------------------------- #

def test_select_engine_precedence(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("EPUB2MD_JUDGE_ENGINE", raising=False)

    # Key present beats a `claude` on PATH.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(si.shutil, "which", lambda *_: "/usr/local/bin/claude")
    assert si._select_engine() == "api"

    # No keys + CLI on PATH -> cli.
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert si._select_engine() == "cli"

    # No keys + no CLI -> none.
    monkeypatch.setattr(si.shutil, "which", lambda *_: None)
    assert si._select_engine() == "none"

    # Explicit override wins even over a key.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("EPUB2MD_JUDGE_ENGINE", "cli")
    assert si._select_engine() == "cli"


def _has_pair(cmd, flag, value):
    return any(cmd[i] == flag and cmd[i + 1] == value for i in range(len(cmd) - 1))


def test_cli_engine_happy_path(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout=_envelope(_CLEAN_REPORT), stderr="")

    monkeypatch.setattr(si.subprocess, "run", fake_run)
    report = si._judge_via_claude_cli(si.DEFAULT_MODEL, "REF-SENTINEL text", "{}", "MD-SENTINEL text")
    assert report == JudgeReport.model_validate(_CLEAN_REPORT)

    (cmd, kwargs), = calls
    assert "-p" in cmd
    assert _has_pair(cmd, "--output-format", "json")
    assert "--json-schema" in cmd and "--system-prompt" in cmd
    assert _has_pair(cmd, "--model", si.DEFAULT_MODEL)
    assert _has_pair(cmd, "--tools", "")
    assert "--no-session-persistence" in cmd
    assert _has_pair(cmd, "--effort", "high")
    assert _has_pair(cmd, "--setting-sources", "user")
    assert "--bare" not in cmd  # would restrict auth to API keys, breaking the OAuth path
    # ARG_MAX guarantee: the big texts ride stdin, never argv.
    assert all("REF-SENTINEL" not in a and "MD-SENTINEL" not in a for a in cmd)
    assert "REF-SENTINEL" in kwargs["input"] and "MD-SENTINEL" in kwargs["input"]


def test_cli_engine_result_string_fallback(monkeypatch):
    # Older CLI / schema tool not engaged: no structured_output, result is a JSON string.
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=_envelope(None, result_str=_json.dumps(_CLEAN_REPORT)), stderr=""
        )

    monkeypatch.setattr(si.subprocess, "run", fake_run)
    report = si._judge_via_claude_cli(si.DEFAULT_MODEL, "ref", "{}", "md")
    assert report == JudgeReport.model_validate(_CLEAN_REPORT)


_TIMEOUT = subprocess.TimeoutExpired(["claude", "-p"], 600)

_FAIL_VARIANTS = {
    "stdout_not_json": (lambda cmd: subprocess.CompletedProcess(cmd, 0, stdout="garbage", stderr=""), None),
    "subtype_error": (lambda cmd: subprocess.CompletedProcess(
        cmd, 0, stdout=_envelope(_CLEAN_REPORT, subtype="error_max_turns"), stderr=""), None),
    "is_error_true": (lambda cmd: subprocess.CompletedProcess(
        cmd, 0, stdout=_envelope(_CLEAN_REPORT, is_error=True), stderr=""), None),
    "result_is_prose": (lambda cmd: subprocess.CompletedProcess(
        cmd, 0, stdout=_envelope(None, result_str="Sorry, I could not produce JSON."), stderr=""), None),
    "schema_violation": (lambda cmd: subprocess.CompletedProcess(
        cmd, 0, stdout=_envelope({"findings": "nope"}), stderr=""), None),
    "nonzero_exit": (lambda cmd: subprocess.CompletedProcess(
        cmd, 1, stdout="", stderr="boom: OAuth token expired"), "OAuth token expired"),
    "timeout": (_TIMEOUT, None),
}


@pytest.mark.parametrize("variant", sorted(_FAIL_VARIANTS))
def test_cli_engine_fails_closed(monkeypatch, tmp_path, synthetic_epub, variant):
    behavior, expect_msg = _FAIL_VARIANTS[variant]

    def fake_run(cmd, **kwargs):
        if isinstance(behavior, Exception):
            raise behavior
        return behavior(cmd)

    monkeypatch.setattr(si.subprocess, "run", fake_run)

    # The engine itself raises...
    with pytest.raises(Exception) as excinfo:
        si._judge_via_claude_cli(si.DEFAULT_MODEL, "ref", "{}", "md")
    if expect_msg:
        assert expect_msg in str(excinfo.value)

    # ...and the orchestrator fails closed: error status, zero filings.
    _force_cli_engine(monkeypatch)
    _redirect_history(monkeypatch, tmp_path)
    epub, md = synthetic_epub
    result = si.evaluate_conversion(epub, md, dry_run=True, logger=lambda *a: None)
    assert result["status"] == "error" and result["engine"] == "cli"
    assert "filed" not in result and "outcomes" not in result


def test_cli_engine_chunked_resilience(monkeypatch, synthetic_epub):
    monkeypatch.setattr(si, "SINGLE_PASS_CHARS", 0)  # force the chunked path
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # first chunk fails; the rest succeed
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="transient failure")
        return subprocess.CompletedProcess(cmd, 0, stdout=_envelope(_CLEAN_REPORT), stderr="")

    monkeypatch.setattr(si.subprocess, "run", fake_run)
    epub, md = synthetic_epub
    reports = si.run_judge(epub, md, _signals(), si.DEFAULT_MODEL, engine="cli", logger=lambda *a: None)
    assert calls["n"] >= 2
    assert len(reports) == calls["n"] - 1  # one bad chunk dropped, survivors kept


def test_cli_engine_needs_no_sdk(monkeypatch, tmp_path, synthetic_epub):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # `import anthropic` would raise
    _force_cli_engine(monkeypatch)
    _redirect_history(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=_envelope(_CLEAN_REPORT), stderr="")

    monkeypatch.setattr(si.subprocess, "run", fake_run)
    epub, md = synthetic_epub
    result = si.evaluate_conversion(epub, md, dry_run=True, logger=lambda *a: None)
    assert result["status"] == "ok" and result["engine"] == "cli"
