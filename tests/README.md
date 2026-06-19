# Regression test suite

This suite is the **auto-merge gate** for the self-improvement loop: a fix only
merges to `main` if `pytest -q` is green. Run it with:

```bash
pip install -e ".[dev]"   # pytest (+ anthropic)
pytest -q
```

## What runs where

| Layer | CI | Local |
|---|---|---|
| Oracle unit tests (scoring functions on synthetic Markdown) | ✅ | ✅ |
| End-to-end synthetic EPUB conversion | ✅ | ✅ |
| Corpus floors/ceilings (real sample books) | ✅ (EPUBs are committed) | ✅ |
| Baseline-tamper guard (vs `origin/main`) | ✅ | ✅ |

The sample EPUBs are committed to the repo, so the corpus tests run **in CI too**
(they only `pytest.skip` if the EPUBs are removed). Because the auto-merge gate
runs in CI, **`baselines.json` is calibrated to CI's (Ubuntu) pandoc**, which can
differ from a local macOS pandoc — keep the floors comfortably below the lowest
of the two so the gate catches real regressions without false failures.

## Sample corpus

Place the test EPUBs in `sample-epubs-for-testing/` (already gitignored):

- `7 Powers*.epub` — suboptimal book (div artifacts, 0 headings today).
- `Venture Deals*.epub` — well-formed book (page-nav removal).

Baselines for these live in `tests/baselines.json` (committed — numbers only, no
book content). Regenerate them from current converter output after an intentional
quality change:

```bash
pytest --regen-baselines     # local only; reconverts the corpus and rewrites baselines.json
```

The baseline-tamper guard (`test_baselines_not_loosened`) fails CI if a floor is
lowered or an artifact ceiling raised versus `main`, so an autonomous fix cannot
weaken the gate to make itself pass. Tighten baselines after a real improvement;
never loosen them to get a regression to pass.
