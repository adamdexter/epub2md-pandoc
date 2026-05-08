# Changelog

All notable changes to epub2md-pandoc are tracked here.

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
