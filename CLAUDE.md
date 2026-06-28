# CLAUDE.md

Guidance for working in this repo. For deeper internals see `ARCHITECTURE.md`; user-facing docs are in `README.md` and `QUICKSTART.md`.

## What this is

`epub2md` — converts **EPUB, PDF, and web articles** to AI-optimized Markdown for Claude Projects and RAG systems. Output is cleaned to maximize RAG search quality and minimize tokens (~30–40% smaller than raw Pandoc), with YAML frontmatter (title/author/year) and proper `#`/`##`/`###` heading hierarchy.

The repo dir is named `epub2md-pandoc`; the product/package name is `epub2md`. The name stays — don't "fix" it.

## Modules

- `epub_to_md_converter.py` — core EPUB→MD. Pandoc integration, metadata extraction, two-phase artifact cleanup, EPUB quality pre-check, Calibre + TOC-anchor heading recovery. Entry: `process_folder()`, `main()`.
- `pdf_to_md_converter.py` — PDF→MD (pdf2md). Analysis-first routing, native + scanned (OCR) PDFs, table extraction. Entry: `convert_pdf_to_markdown()`, `main()`.
- `html_to_md_converter.py` — web article→MD. URL fetch, content extraction, image download. Entry: `main()`.
- `medium_scraper.py` — optional Medium auth/scraping via Selenium + undetected-chromedriver. Feature-flagged behind try/except import (`MEDIUM_SUPPORT_AVAILABLE`); core works without it.
- `reddit_browser.py` — optional Reddit real-browser fallback via **nodriver** (modern successor to undetected-chromedriver). Used only when the plain JSON fetch is blocked by Reddit's "Please wait for verification" bot-check: a real Chrome with a persistent profile (`.reddit_chrome_profile`) passes the gate, then an in-page `fetch()` of the `.json` feeds the existing `reddit_json_to_markdown()` parser. Feature-flagged behind try/except import (`REDDIT_BROWSER_AVAILABLE`); core works without it.
- `gui.py` — Flask web GUI. Imports the converters; serves `templates/index.html`. Entry: `main()`.
- `self_improve.py` — **self-improvement mode** (experimental, opt-in). LLM-as-judge (Anthropic SDK, lazy-imported) comparing an EPUB to its Markdown, filing de-duplicated GitHub issues; dedup ledger / caps / circuit-breaker in `~/.epub2md_eval_history.json`. Entry: `evaluate_conversion()`, CLI `python self_improve.py <epub> <md> --dry-run`.
- `epub_text.py` — spine-aware plain-text extraction from EPUBs (reference text for the judge).
- `version.py` — **single source of truth** for the version (`__version__`). pyproject reads it dynamically; `gui.py` and the HTML header read it at runtime. Bump here only.

Dependency graph: `gui.py → {epub, pdf, html}`, and `html → {medium_scraper, reddit_browser} (both optional)`.

## Run & develop

```bash
./install.sh          # creates .venv, checks Python 3.9+ & Pandoc, installs deps
./run_gui.sh          # activates .venv, runs gui.py → http://localhost:3763  (3763 = "EPMD")
```

CLI entry points (also exposed as console scripts `epub2md` / `pdf2md` / `html2md` / `epub2md-gui`):

```bash
python3 epub_to_md_converter.py /path/to/epub/folder [output_folder]
python3 pdf_to_md_converter.py  /path/to/pdf
python3 html_to_md_converter.py https://example.com/article
```

Always run via the project `.venv`. Sample EPUBs for manual testing live in `sample-epubs-for-testing/`.

## Conventions

- **External dep: Pandoc** must be installed (`brew install pandoc`). Code guards with `check_pandoc_installed()`.
- **Unified versioning (since v3.0.0):** one version across the whole project in `version.py`. Note `ARCHITECTURE.md`'s "Version Numbering" section is stale — it describes the old split GUI-2.x / converter-1.x scheme that no longer applies.
- **Graceful degradation:** heavy/optional deps (Selenium, OCR) are import-guarded so the core never hard-fails. Bare `except` is used intentionally for this (ruff ignores `E722`).
- **Python 3.12+ distutils shim** in `medium_scraper.py` must run *before* importing undetected-chromedriver.
- **Lint:** ruff, line-length 120, target py39 (`E`, `F`, `I`, `UP`). Ignores `E501`, `E722`, `F841`.
- Update `CHANGELOG.md` for user-facing changes; keep `README.md` in sync for new flags/features.

## Quick checks

```bash
python3 -m py_compile epub_to_md_converter.py pdf_to_md_converter.py html_to_md_converter.py medium_scraper.py gui.py self_improve.py epub_text.py
ruff check .          # must be clean (lint is a CI gate)
pytest -q             # regression suite (install: pip install -e ".[dev]")
```

**Tests:** `pytest -q` runs the suite in `tests/` — oracle unit tests + a synthetic-EPUB end-to-end conversion (both run in CI), optional real-corpus floors/ceilings (skipped when `sample-epubs-for-testing/*.epub` are absent), the self-improvement unit tests, and a baseline-tamper guard. This suite is the **auto-merge gate** for the self-improvement loop, so don't loosen `tests/baselines.json`; regenerate it with `pytest --regen-baselines` only after an *intentional* quality change.

## Self-Improvement mode (experimental)

Toggle in the EPUB tab. When on, after each conversion `self_improve.py` judges EPUB↔Markdown fidelity and files GitHub issues labelled `self-improvement`; `.github/workflows/self-improve.yml` (Claude Code Action) implements a fix, runs the suite + ruff, opens a PR, and **auto-merges on green CI**. The regression suite is the only safety gate — backed by a baseline-tamper guard and a CI `scope-guard` (the coder can't edit `.github/`), a dedup ledger / caps / circuit-breaker, and the toggle as a kill switch. Needs `ANTHROPIC_API_KEY` (local env for the judge; repo secret for the Action) and the Claude GitHub App installed. `anthropic` is lazy-imported, so the base app runs without it. Full design: `.claude/plans/ok-now-i-have-dynamic-galaxy.md`.

## Gitignored runtime state

`.venv/`, `.medium_cookies/`, `.medium_chrome_profile/`, `.reddit_chrome_profile/` (nodriver's Reddit profile), and generated `md processed books/` output folders. Never commit session cookies or the Chrome profiles. The self-improvement eval history/ledger lives at `~/.epub2md_eval_history.json` (home dir, not the repo).
