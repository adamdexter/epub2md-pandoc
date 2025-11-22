# EPUB to Markdown Batch Converter

Automatically converts EPUB files to Markdown with AI-optimized filenames that include book metadata.

## Features

- ✅ **Batch processes** all EPUB files in a folder
- 📖 **Extracts metadata** (title, author, year, edition) from EPUB files
- 🤖 **AI-optimized filenames** without special characters (parentheses, brackets)
- 🧹 **Claude-optimized markdown** - automatically cleans up for RAG performance
- 📝 **Proper heading hierarchy** using # ## ### syntax
- 🎯 **Metadata headers** added to each file (YAML frontmatter)
- 🚫 **Removes artifacts**: Pandoc divs, HTML anchors, broken image links
- 📊 **Reports file size** to help monitor token efficiency
- 🔄 **Preserves content** while removing formatting noise

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

Suggestions and improvements welcome! Common enhancements:
- Support for multiple languages
- Batch processing with parallel conversions
- GUI version
- Additional metadata extraction (ISBN, publisher, etc.)
