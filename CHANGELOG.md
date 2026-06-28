# Changelog

All notable changes to epub2md-pandoc are tracked here.

## [Unreleased]

### Fixed
- **Garbled output / failed extraction on brotli-serving sites** — the web-article
  fetcher advertised `Accept-Encoding: ...br` but couldn't decode brotli unless the
  optional `brotli` package was installed. Affected servers (e.g. domyown.com) then
  returned brotli-compressed bytes that got mis-decoded into binary garbage, so every
  extractor failed and the converter produced empty/incorrect output (this also made
  multi-page captures look broken). The fetcher now advertises only the compressions
  it can actually decode (detected at runtime), decodes a stray brotli/zstd response
  itself when possible, and otherwise fails with a clear "install the decoder" message.
  `brotli` is now a declared dependency for native decoding.

### Added
- **Reddit posts** — Reddit pages are served behind a JavaScript bot-check
  ("Please wait for verification"), so the generic HTML extractors only ever
  saw the interstitial and produced no content. Reddit URLs are now detected
  and routed through Reddit's public JSON API instead, rendering the post body
  and (nested) comments to Markdown with author/subreddit/date metadata.
  Handles self/link/gallery posts and `/s/` share links; surfaces a clear
  message when Reddit rate-limits the request.
- **Paginated web articles** — the web-article converter can now follow
  pagination query parameters (`?page=`, `?pg=`, `?paged=`, etc.) and combine
  multiple pages into a single Markdown file:
  - When a URL with a pagination parameter is detected, the GUI reveals a
    "Pages to capture" field and the CLI prompts for a page count.
  - Capture starts from the page number in the URL and increments. For example,
    a URL ending in `?page=2` with a count of `3` captures pages 2, 3, and 4.
  - New `--pages N` flag on `html_to_md_converter.py` for non-interactive use;
    `convert_url_to_markdown()` gains a `page_count` parameter. Images on later
    pages are also captured and de-duplicated.

## [3.2.0] - 2026-06-19

### Added
- **Self-Improvement mode (experimental)** — a toggle-able loop that lets the
  converter detect and fix its own conversion-quality regressions:
  - When enabled (EPUB tab toggle), after each conversion an LLM-as-judge
    (`self_improve.py`, Anthropic SDK, `claude-opus-4-8` by default / `claude-sonnet-4-6`
    as a cost option) compares the original EPUB's reference text to the produced
    Markdown and returns structured findings (`messages.parse` + Pydantic schema).
  - Real problems are filed as de-duplicated GitHub issues labelled `self-improvement`;
    a Claude Code GitHub Action (`.github/workflows/self-improve.yml`) implements the
    fix, runs the regression suite + ruff, opens a PR, and **auto-merges on green CI**.
  - Safety rails: a regression test suite (`tests/`) as the merge gate, a
    baseline-tamper guard, a CI scope-guard (the coder can't edit CI/workflows), a
    dedup ledger + per-run/per-day caps + a circuit breaker (routes risky/recurring
    findings to a `self-improvement-hold` label) in `~/.epub2md_eval_history.json`,
    and the toggle itself as a kill switch.
- **New `epub_text.py`** — spine-aware plain-text extraction from EPUBs for the judge.
- **Regression test suite (`tests/`)** — pytest harness with a synthetic-EPUB
  end-to-end conversion (runs in CI), oracle unit tests, optional real-corpus
  floors/ceilings, and the baseline-tamper guard. `pytest -q` is now the CI gate.
- `epub_to_md_converter.process_folder()` now returns `(epub_path, md_path)` pairs;
  new `collect_quality_signals()` shares one quality oracle between the judge and tests.

### Fixed
- **CI workflow was malformed YAML** (an inline `run:` step contained a colon-space
  inside an unquoted scalar) and had never run successfully — converted to block
  scalars so CI executes.
- Cleared all pre-existing `ruff` violations repo-wide so `lint` passes.

## [3.1.0] - 2026-05-07

### Added
- **TOC-anchored chapter detection** for EPUBs that ship without real `<h1>`/`<h2>` headings.
  Some publishers (e.g., the Sway EPUBs from Anna's Archive) place chapter titles only in
  the table of contents as `[**Chapter X**...](#anchor)` links, with bare `[]{#anchor}`
  markers in chapter bodies. Pandoc therefore produced 0 markdown headings, causing the
  quality pre-check to flag these EPUBs as `CRITICAL` and skip them entirely.
  - New `build_toc_anchor_map()` parses TOC links and builds an `{anchor_id: heading_text}`
    mapping, pulling subtitles from `[...]{.ss1}` (or any `{.class}`) styled spans.
  - New `apply_toc_anchor_headings()` inserts `# heading` lines at each `[]{#anchor}`
    marker found in the body.
  - Quality pre-check (`assess_epub_quality`) now treats this pattern as auto-fixable
    when 3+ TOC anchors match body markers — score penalty drops from −40 to −15
    (typically lifts these EPUBs from ~60% to 85%, allowing them to proceed).
- EPUB-tab input UI redesigned around a single drag-and-drop area:
  - Drag and drop now accepts files, folders, or multiple items at once.
  - "Or manually select files or a folder via system window" link opens native pickers
    (`Choose Files` for multi-select files, `Choose Folder` for a single folder).
  - Selected items render inline in the drop zone with × to remove individually.
  - The legacy "Input Folder" text field/Browse button is gone — it caused confusion
    because dragged files were copied into that folder, then the conversion processed
    *both* the dragged files and any pre-existing files in the folder.
- Backend: new `/native_files_dialog` endpoint (multi-file picker via `osascript`/tkinter).
- Backend: `/upload_file` now stages to a server-managed temp dir when no target folder is
  given, so dragged files don't pollute user-chosen directories.
- Backend: `/convert` now accepts an `items` list (mixed paths + staged uploads) and stages
  everything into a fresh temp work dir before running the EPUB pipeline.

### Fixed
- HTML header now reads the version dynamically from `version.py` instead of the
  hardcoded `2.7.0` string.

## [3.0.0] - 2026-04-27

### Added
- Unified versioning across all modules via `version.py` (replaces 4 separate
  `CONVERTER_VERSION` strings in epub/html/pdf/gui modules).
- PyPI packaging via `pyproject.toml` with CLI entry points: `epub2md`, `html2md`,
  `pdf2md`, `epub2md-gui`. Optional dependency groups: `medium`, `ocr`, `all`.
- Docker support: `Dockerfile` (Python 3.12-slim + Pandoc + Tesseract) and `.dockerignore`.
- GitHub Actions CI: ruff lint + smoke tests on Python 3.10/3.12/3.13.

### Fixed
- Folder preferences now persist `url_output_folder` and `pdf_output_folder` (backend
  was silently dropping them).
- URL input field now styled at full width (`input[type="url"]` was missing from CSS).
- Medium scraper now tries headless first with saved cookies, only opening a visible
  browser when manual login is required. All prints flush in real time for the GUI log.
- Image downloading: relaxed the over-aggressive filter; images now save in
  article-specific subdirectories (`article_images/{article-title}/`); unreferenced
  images are appended to the markdown.
- Medium author extraction: replaced strict First-Last regex with a flexible
  `_is_name_like()` helper. Profile-link text is prioritized as the most reliable source.
- Output file paths now display in styled `<code>` blocks with `user-select:all` so users
  can click-to-select-and-copy.
- WSJ/paywall handling: explicit detection of paywall sites and gift-link tokens, with
  session-based requests using `Referer` headers and clear error messages.

### Changed
- Consolidated 3 duplicate `OutputCapture` classes in `gui.py` into one reusable class.
- Removed 5 redundant `import re` statements.
- Upgraded EPUB CLI from raw `sys.argv` parsing to `argparse` (now supports `--help`).
