# EPUB to Markdown Batch Converter

Automatically converts EPUB files to Markdown with AI-optimized filenames that include book metadata.

## Features

- ✅ Batch processes all EPUB files in a folder
- 📖 Extracts metadata (title, author, year, edition) from EPUB files
- 🤖 Creates AI-optimized filenames for easy reference
- 🔄 Preserves document structure and hierarchy
- 📝 Outputs clean Markdown with ATX-style headings
- 🖼️ Extracts embedded images

## Filename Format

Output files follow this AI-optimized format:
```
Title - Author (Year) [Edition].md
```

**Examples:**
- `Atomic Habits - James Clear (2018).md`
- `Deep Work - Cal Newport (2016).md`
- `Python Crash Course - Eric Matthes (2019) [2nd Edition].md`

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

Then open your browser to: **http://localhost:5000**

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

Then open http://localhost:5000 in your browser.

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

## What Gets Preserved

The conversion maintains:
- ✅ Chapter hierarchy (H1, H2, H3, etc.)
- ✅ Text formatting (bold, italic)
- ✅ Lists (ordered and unordered)
- ✅ Links
- ✅ Block quotes
- ✅ Code blocks
- ✅ Images (extracted separately)

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

## Use Case: Claude Projects

This script is optimized for uploading books to Claude Projects:

1. **AI-friendly filenames** - Claude can easily identify books by title, author, and year
2. **Clean Markdown** - Optimal format for Claude's RAG system
3. **Preserved structure** - Maintains chapter hierarchy for accurate searching
4. **Efficient tokens** - Markdown uses fewer tokens than PDF format

After conversion, simply drag the `.md` files into your Claude Project's knowledge base!

## License

MIT License - Feel free to modify and use as needed.

## Contributing

Suggestions and improvements welcome! Common enhancements:
- Support for multiple languages
- Batch processing with parallel conversions
- GUI version
- Additional metadata extraction (ISBN, publisher, etc.)
