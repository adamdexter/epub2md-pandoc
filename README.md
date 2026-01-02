# EPUB & Web to Markdown Converter

Convert EPUB files and web articles to AI-optimized Markdown for Claude Projects and RAG systems.

## Features

### EPUB Conversion
- ✅ **Batch processes** all EPUB files in a folder
- 📖 **Extracts metadata** (title, author, year, edition) from EPUB files
- 🤖 **AI-optimized filenames** without special characters (parentheses, brackets)
- 🧹 **Claude-optimized markdown** - automatically cleans up for RAG performance
- 🔍 **EPUB quality pre-check** - analyzes files BEFORE conversion to detect issues
- 🔄 **Auto-converts Calibre headings** - fixes `[TEXT]{.calibre}` markers automatically
- 🎯 **Smart artifact detection** - analyzes and scores files before cleanup
- 📈 **Conditional cleanup** - only applies cleanup when needed (< 85% score)
- 📝 **Proper heading hierarchy** using # ## ### syntax
- 🎯 **Metadata headers** added to each file (YAML frontmatter with version tracking)
- 🚫 **Removes 7 types of artifacts**: header IDs, HTML blocks, citations, etc.
- 📊 **Detailed reporting** - shows artifacts found, scores, and improvements
- 🔄 **Preserves optimal files** - skips unnecessary cleanup for clean EPUBs
- ⚙️ **Configurable thresholds** - adjust quality requirements to your needs

### Web Article Conversion
- 🌐 **Convert any web article** to clean Markdown
- 📰 **Medium article support** with authenticated access (member-only content)
- 🖼️ **Downloads images** locally for offline access
- 📝 **Extracts metadata** (title, author, publication date)
- 🧹 **Cleans HTML** - removes ads, navigation, and clutter
- 🔗 **Preserves links** and formatting

## Filename Format

Output files follow this AI-optimized format (no special characters):
```
Title - Author Year Edition.md
```

**Examples:**
- `Atomic Habits - James Clear 2018.md`
- `Deep Work - Cal Newport 2016.md`
- `Python Crash Course - Eric Matthes 2019 2nd Ed.md`
- `7 Powers - Hamilton Helmer 2016.md`

**Key improvements:**
- ✅ No parentheses or brackets (better file system compatibility)
- ✅ Edition numbers properly extracted and included
- ✅ Year always included when available
- ✅ Clean, easy-to-read format

## Requirements

### 1. Python 3.6+
Check your Python version:
```bash
python3 --version
```

### 2. Pandoc
**macOS:**
```bash
brew install pandoc
```

**Ubuntu/Debian:**
```bash
sudo apt-get install pandoc
```

**Windows:**
Download from [pandoc.org](https://pandoc.org/installing.html)

**Verify installation:**
```bash
pandoc --version
```

## Quick Start (Recommended)

### 1. Install Dependencies

**Linux/macOS:**
```bash
./install.sh
```

**Windows:**
```bash
install.bat
```

The installer will:
- ✅ Check for Python 3 installation
- ✅ Check for Pandoc installation
- ✅ Create a virtual environment (avoids system package conflicts)
- ✅ Install Flask in the virtual environment
- ✅ Create launcher scripts
- ✅ Make all scripts executable

### 2. Run the GUI

**Linux/macOS:**
```bash
./run_gui.sh
```

**Windows:**
```bash
run_gui.bat
```

Then open your browser to: **http://localhost:3763**

> 💡 **Port 3763?** On a phone keypad, 3763 spells "EPMD" (EPUB to MarkDown) - making it easy to remember!

The GUI provides:
- 📁 Folder browser for easy selection
- 📊 Real-time conversion progress
- 🎨 Beautiful, user-friendly interface
- ✅ Visual feedback and status updates

---

## Manual Installation

If you prefer to install dependencies manually:

1. Download the script:
```bash
curl -O https://raw.githubusercontent.com/[your-repo]/epub_to_md_converter.py
```

Or simply save the `epub_to_md_converter.py` file to your computer.

2. Make it executable (macOS/Linux):
```bash
chmod +x epub_to_md_converter.py
```

## Usage

### Option 1: Web GUI (Recommended)

Run the GUI for an easy-to-use interface:

**Linux/macOS:**
```bash
./run_gui.sh
```

**Windows:**
```bash
run_gui.bat
```

Then open http://localhost:3763 in your browser.

### Option 2: Command Line

Place your EPUB files in a folder, then run:

```bash
python3 epub_to_md_converter.py /path/to/epub/folder
```

This will:
- Process all `.epub` files in the specified folder
- Create a new folder called `md processed books`
- Convert each EPUB to Markdown with optimized naming

### Custom Output Folder

Specify a custom output folder:

```bash
python3 epub_to_md_converter.py /path/to/epub/folder /path/to/output/folder
```

### Examples

**Example 1: Basic conversion**
```bash
python3 epub_to_md_converter.py ~/Downloads/ebooks
```

**Example 2: Custom output location**
```bash
python3 epub_to_md_converter.py ~/Books/epub ~/Books/markdown
```

**Example 3: Current directory**
```bash
python3 epub_to_md_converter.py .
```

---

## Web Article Conversion

Convert web articles (blog posts, news articles, Medium posts) to Markdown.

### Basic Usage

**Via GUI (Recommended):**
1. Run `./run_gui.sh` (Linux/macOS) or `run_gui.bat` (Windows)
2. Open http://localhost:3763
3. Click the "Web Articles" tab
4. Paste a URL and click Convert

**Via Command Line:**
```bash
python3 html_to_md_converter.py https://example.com/article
```

### Medium Articles (Authenticated Access)

Medium gates full article content behind a paywall. This converter supports authenticated access to read member-only articles using your Medium account.

#### How It Works

1. **First-time setup**: When you convert a Medium article, a browser window opens
2. **Log in once**: Sign in to Medium using your email, Google, or other method
3. **Session saved**: Your session is saved locally for future conversions
4. **Full content**: All future Medium conversions use your saved session

#### Using Your Medium Credentials

When converting a Medium article for the first time:

```
============================================================
Converting: https://medium.com/@author/article-title-abc123
============================================================

[Medium Detected] Using Selenium for authenticated access...
      Opening browser...
      Using undetected-chromedriver (Cloudflare bypass mode)

      ╔════════════════════════════════════════════════════════╗
      ║  MEDIUM LOGIN REQUIRED                                  ║
      ║  Please log in to Medium in the browser window.         ║
      ║  You have 3 minutes to complete login.                  ║
      ║  Your session will be saved for future use.             ║
      ╚════════════════════════════════════════════════════════╝
```

**Login options:**
- Email/Password
- Google Sign-In
- Apple Sign-In
- Facebook Sign-In

After logging in, the browser will automatically fetch the article and close.

#### Session Persistence

Your Medium session is stored locally in:
- `.medium_cookies/` - Session cookies
- `.medium_chrome_profile/` - Browser profile

These are gitignored and never uploaded. Delete these folders to log out.

#### Cloudflare Protection

Medium uses Cloudflare protection. The converter uses `undetected-chromedriver` to bypass this automatically. If you encounter Cloudflare loops:

1. Delete `.medium_chrome_profile/` folder
2. Try again - you may need to solve a CAPTCHA once
3. Ensure Chrome is installed on your system

#### Converting Medium Articles

**Single article:**
```bash
python3 html_to_md_converter.py https://medium.com/@author/article-title-abc123
```

**Via GUI:**
1. Paste the Medium URL in the Web Articles tab
2. Click Convert
3. Log in when prompted (first time only)
4. Article is saved as Markdown

#### Privacy & Security

- Your credentials are **never stored** by this tool
- Only session cookies are saved (same as your browser)
- All data stays on your local machine
- Delete `.medium_cookies/` and `.medium_chrome_profile/` to clear all session data

---

## Output

The script provides detailed progress information:

```
Found 3 EPUB file(s) to convert.

[1/3] Processing: atomic-habits.epub
  📖 Title: Atomic Habits
  ✍️  Author: James Clear
  📅 Year: 2018
  ➡️  Output: Atomic Habits - James Clear (2018).md
  ✅ Conversion successful!

[2/3] Processing: deep-work.epub
  📖 Title: Deep Work
  ✍️  Author: Cal Newport
  📅 Year: 2016
  ➡️  Output: Deep Work - Cal Newport (2016).md
  ✅ Conversion successful!

============================================================
Conversion complete!
✅ Successful: 2
📁 Output folder: /Users/adam/md processed books
```

## Smart Artifact Detection & Cleanup

This converter uses a **two-phase intelligent cleanup system** that adapts to each EPUB's quality:

### Phase 1: Analysis & Scoring

Before cleanup, the converter analyzes the markdown for 7 types of artifacts:

1. **Header IDs** - `## Title {#id .class}` (High impact: -0.5 pts per 1000 lines)
2. **HTML blocks** - ` ``{=html} ` markers (High impact: -2.0 pts)
3. **Complex citations** - `[[2020](#link){.biblioref}]` (Medium impact: -0.2 pts)
4. **Image attributes** - `![](img.jpg){.class}` (Low impact: -0.1 pts)
5. **Bracket classes** - `[Text]{.className}` (Medium impact: -0.3 pts)
6. **XHTML links** - `[Link](#file.xhtml)` (Low impact: -0.1 pts)
7. **Blockquote divs** - `> ::: {}` (Low impact: -0.05 pts)

**Optimization Score** = 100% - (artifact density penalties)

### Phase 2: Conditional Cleanup

- **Score ≥ 85%**: File is already optimal → Skip cleanup, add metadata only
- **Score < 85%**: File needs help → Aggressive cleanup + Standard cleanup

### Example Output

**For well-formatted EPUB (e.g., Venture Deals):**
```
🔍 Analyzing artifacts...
📈 Optimization score: 98.2%
✅ Already optimal (≥ 85%) - Skipping cleanup, adding metadata only...
📊 File size: 245.3 KB
🎯 Reduced by: 0.2%
📑 Headings found: 87
🎉 Ready for Claude Projects!
```

**For suboptimal EPUB (e.g., academic publisher):**
```
🔍 Analyzing artifacts...
📋 Artifacts detected:
   • Header IDs: 371
   • HTML blocks: 26
   • Citations: 200
   • Image attributes: 45
📈 Optimization score: 55.2%
🧹 Cleanup required (< 85%) - Running aggressive cleanup...
✨ Post-cleanup score: 91.3%
📊 File size: 312.7 KB
🎯 Reduced by: 38.4%
📑 Headings found: 142
🎉 Ready for Claude Projects!
```

## Automatic Calibre Heading Conversion

The converter automatically detects and fixes EPUBs with **Calibre-style heading markers** that prevent proper heading detection.

### The Problem

Some EPUB files (especially older Calibre conversions) use styled text markers instead of proper markdown headings:

```markdown
[**CHAPTER 1**]{.calibre3}
[**THE FOUNDATION FOR COACHING**]{.calibre3}
[Getting Started]{.calibre5}
```

These appear as regular text instead of headings, making the document hard to navigate.

### Automatic Detection

The pre-check system detects these patterns and recognizes them as **fixable issues**:

```
🔍 Running quality pre-check...
   Quality Score: 85.0% (threshold: 70.0%)
   Issues detected:
     • Fixable: 166 Calibre-style markers detected (will auto-convert to headings)
   ⚠️  Issues detected but above threshold - proceeding
```

### Automatic Conversion

The converter automatically transforms Calibre markers into proper markdown headings:

**Before:**
```markdown
[**CHAPTER 1**]{.calibre3}
[**THE FOUNDATION FOR COACHING**]{.calibre3}
[**Exploring the Landscape**]{.calibre5}
[Getting Started]{.calibre7}
```

**After:**
```markdown
# CHAPTER 1
## THE FOUNDATION FOR COACHING
### Exploring the Landscape
#### Getting Started
```

### Smart Level Detection

Heading levels are determined automatically:
- **Level 1 (#)**: CHAPTER, PART, INTRODUCTION, major sections
- **Level 2 (##)**: Long bold headings (>35 characters)
- **Level 3 (###)**: Medium/short bold headings (>20 characters)
- **Level 4 (####)**: Plain text headings

### Conversion Output

```
🔍 Analyzing artifacts...
   → Auto-converted 166 Calibre-style headings to markdown
📈 Optimization score: 85.7%
✅ File already optimal (score ≥ 85%)
📊 File size: 424.3 KB
📑 Headings found: 166 ← Properly converted!
🎉 Ready for Claude Projects!
```

### No Manual Steps Required

The conversion happens **automatically** during processing - no manual intervention needed!

## EPUB Quality Pre-Check

The converter includes a smart pre-check system that analyzes EPUBs **before conversion** to detect potential issues:

### Quality Assessment

Before converting, each EPUB is scored based on:
- **Missing headings** (critical structural issue)
- **Heavy HTML artifacts** (formatting noise)
- **Role attributes** and other metadata bloat
- **Calibre-style markers** (auto-fixable)

### Smart Recommendations

Files are categorized and handled appropriately:

**Good Quality (≥70%):**
```
🔍 Running quality pre-check...
   Quality Score: 95.3% (threshold: 70.0%)
   ✓ Quality check passed

🔄 Converting EPUB to Markdown...
```

**Auto-Fixable Issues:**
```
🔍 Running quality pre-check...
   Quality Score: 85.0% (threshold: 70.0%)
   Issues detected:
     • Fixable: 166 Calibre-style markers detected (will auto-convert to headings)
   ⚠️  Issues detected but above threshold - proceeding
```

**Critical Issues:**
```
🔍 Running quality pre-check...
   Quality Score: 60.0% (threshold: 70.0%)
   Issues detected:
     • CRITICAL: Zero headings detected in 4395 lines
     • No auto-fix patterns found

⚠️  QUALITY BELOW THRESHOLD - SKIPPING
   💡 Tip: Manual intervention may be needed
```

### Configurable Behavior

Adjust the threshold in the script:
```python
EPUB_QUALITY_THRESHOLD = 70.0  # Lower = more lenient
SKIP_LOW_QUALITY_EPUBS = True   # Set False to disable pre-check
```

## Claude Project Knowledge Optimization

This converter is **specifically optimized** for uploading to Claude Projects with maximum RAG performance:

### ✅ What Gets Added:
- **Metadata headers** (YAML frontmatter with title, author, year)
- **Proper # headings** (converted from bold text)
- **Clean structure** for better document understanding

### 🚫 What Gets Removed:
- **Page navigation sections** (CRITICAL - saves 10,000+ tokens per book!)
- **Pandoc div artifacts** (`::: booksection`, etc.)
- **HTML anchor tags** (`[]{#id}`)
- **Class annotations** (`{.className}`)
- **Broken image references** (replaced with `[Image removed]`)
- **HTML comments and divs**
- **Verbose list formatting**
- **Escaped apostrophes** (`\'` → `'`)
- **Bracket wrappers** in headings
- **Empty headings**
- **Excessive whitespace**

### 📊 Result:
- **30-40% smaller files** compared to raw Pandoc output
- **Better RAG search** - Claude can find sections accurately
- **Proper hierarchy** - Document structure is preserved
- **Faster processing** - Less noise means faster retrieval
- **More content fits** in Claude's context window

### Example Output:
```markdown
---
title: "7 Powers: The Foundations of Business Strategy"
author: "Hamilton Helmer"
year: 2016
---

# INTRODUCTION

## The Strategy Compass

Strategy is the study of the fundamental determinants...
```

## What Gets Preserved

The conversion maintains:
- ✅ Chapter hierarchy (proper # ## ### headings)
- ✅ Text formatting (bold, italic)
- ✅ Lists (ordered and unordered, cleaned up)
- ✅ Links (inline format)
- ✅ Block quotes
- ✅ Code blocks
- ✅ Tables (in markdown format)
- ✅ All text content

## Troubleshooting

### "Pandoc is not installed"
Install Pandoc using the instructions above and verify with `pandoc --version`

### "No EPUB files found"
- Check that you're pointing to the correct folder
- Ensure files have `.epub` extension (lowercase)

### "Could not extract metadata"
- Some EPUBs have non-standard metadata formats
- The script will use the original filename as a fallback
- The conversion will still proceed

### Filename too long
The script automatically truncates long filenames to ~100 characters at word boundaries.

### Special characters in filenames
The script automatically removes filesystem-unsafe characters like `<>:"/\|?*`

### Medium: Stuck in Cloudflare loop
1. Delete the `.medium_chrome_profile/` folder
2. Run the conversion again
3. You may need to solve a CAPTCHA once manually
4. Ensure you have Chrome installed (not just Chromium)

### Medium: "Another session is running"
Only one Medium conversion can run at a time. Close other browser windows using the Medium profile, or wait for the current conversion to complete.

### Medium: Login not detected
If the converter doesn't detect your login:
1. After logging in, navigate to any Medium article
2. Wait a few seconds on the article page
3. The converter will detect you're logged in and proceed

### Medium: distutils not found (Python 3.12+)
Run `pip install setuptools` - this provides the distutils compatibility shim needed for Python 3.12 and later.

## Advanced: Modifying the Script

### Change filename format
Edit the `create_ai_optimized_filename()` function to customize the output format.

### Adjust Pandoc options
Modify the `cmd` list in `convert_epub_to_md()` to add/change Pandoc flags:
```python
cmd = [
    'pandoc',
    epub_path,
    '-o', output_path,
    '--markdown-headings=atx',
    '--wrap=none',
    '--toc',  # Add this for table of contents
    '--extract-media=.',
]
```

### Filter by date
Add a date filter to only process recent EPUBs, or books from specific years.

## Testing

Sample EPUBs for testing are in the `sample-epubs-for-testing/` folder. Add your own EPUBs there to test conversion quality.

### Quick Quality Check

After converting, the script reports:
- **File size** in KB
- **Reduction percentage** (how much cleanup was done)
- **Heading count** (should be 50+ for book-length content)

Example output:
```
🧹 Cleaning up markdown for Claude...
📊 File size: 245.3 KB
🎯 Reduced by: 35.2%
📑 Headings found: 87
```

### Manual Verification

```bash
# Count headings (should be 50+ for books)
grep -c "^#" output.md

# Check for artifacts (should all be 0)
grep -c "^:::" output.md
grep -c "\[\]{#" output.md
grep -c "## Pages" output.md
grep -c "{\.\\w" output.md
```

## Quality Checklist

After conversion, your files will automatically pass these quality checks:

### ✅ MUST HAVE (Essential for RAG)
- [x] **Proper heading hierarchy** using # ## ### syntax
- [x] **No HTML div artifacts** (no `:::`, `::::`, etc.)
- [x] **No HTML anchor tags** (no `[]{#id}`)
- [x] **Clean list formatting** (standard markdown lists)
- [x] **No broken image links** (images removed or placeholders used)
- [x] **Standard markdown syntax only**

### ✅ RECOMMENDED (Included)
- [x] **Metadata header** with title, author, year
- [x] **Consistent heading levels**
- [x] **Tables in markdown format**
- [x] **Single line breaks** between paragraphs
- [x] **No inline HTML**

### 📊 File Size Targets
- **Good:** 200-300 KB for typical book
- **Excellent:** < 200 KB (very clean conversion)
- **Token efficiency:** ~200-250 tokens per KB

## Use Case: Claude Projects

This script is optimized for uploading books to Claude Projects:

1. **AI-friendly filenames** - Claude can easily identify books by title, author, and year
2. **Clean Markdown** - Optimal format for Claude's RAG system
3. **Preserved structure** - Maintains chapter hierarchy for accurate searching
4. **Efficient tokens** - 30-40% reduction compared to raw Pandoc output
5. **Better search** - No formatting artifacts to confuse retrieval

After conversion, simply drag the `.md` files into your Claude Project's knowledge base!

## License

MIT License - Feel free to modify and use as needed.

## Contributing

Suggestions and improvements welcome! See [ARCHITECTURE.md](ARCHITECTURE.md) for internal documentation.

### Future Enhancements
- Support for additional platforms (LinkedIn, Substack)
- Batch URL processing
- Additional metadata extraction (ISBN, publisher)
- Parallel conversions for large batches

### Adding New Platform Support

The codebase is designed to be extensible. To add support for a new platform:
1. Create a new module (e.g., `linkedin_scraper.py`)
2. Follow the pattern from `medium_scraper.py`
3. Update `html_to_md_converter.py` to import and use the new module

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed instructions.
