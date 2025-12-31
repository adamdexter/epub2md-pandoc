# Sample EPUBs for Testing

Place EPUB files here to test the converter.

## Test Files

For quality assurance, test with these EPUBs:

1. **7 Powers: The Foundations of Business Strategy** by Hamilton Helmer (2017)
   - Tests: Suboptimal EPUB with div artifacts, bold headings
   - Expected issues: Needs aggressive cleanup

2. **Venture Deals** by Brad Feld & Jason Mendelson (4th edition, 2019)
   - Tests: Well-formatted EPUB with page navigation
   - Expected issues: Page navigation section removal

## Usage

```bash
# Convert all EPUBs in this folder
python3 ../epub_to_md_converter.py . ../test-output

# Or use the GUI
../run_gui.sh
```

## Quality Checks

After conversion, verify:

```bash
# Count headings (should be 50+ for books)
grep -c "^#" output.md

# Check for artifacts (should be 0)
grep -c "^:::" output.md
grep -c "\[\]{#" output.md
grep -c "## Pages" output.md

# Check file size (target: 200-300 KB for typical book)
ls -lh output.md
```

## Notes

- EPUB files are not tracked in git (see .gitignore)
- Add your own EPUBs for testing
- Always verify output quality before using in production
