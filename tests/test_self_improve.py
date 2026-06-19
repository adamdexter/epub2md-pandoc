"""Unit tests for the judge orchestration, dedup, caps, and circuit breaker.

No network and no `gh`: the Anthropic client is monkeypatched and all filing runs
in dry-run mode (which never shells out).
"""

import self_improve as si
from epub_to_md_converter import collect_quality_signals
from self_improve import Finding, JudgeReport


def mk(severity="major", category="heading_structure", title="Recover missing headings",
       systemic=True, confidence=0.85):
    return Finding(severity=severity, category=category, title=title, evidence="e",
                   suggested_fix="f", is_systemic=systemic, confidence=confidence)


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
# Orchestrator: skip without key; full run with a mocked client
# --------------------------------------------------------------------------- #

def test_evaluate_skips_without_api_key(monkeypatch, synthetic_epub):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    epub, md = synthetic_epub
    result = si.evaluate_conversion(epub, md, logger=lambda *a: None)
    assert result["status"] == "skipped" and result["reason"] == "no_api_key"


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
    reports = si.run_judge(epub, md, signals, si.DEFAULT_MODEL, logger=lambda *a: None)
    assert reports and isinstance(reports[0], JudgeReport)
    assert si.merge_findings(reports) == []  # acceptable conversion -> nothing to file
