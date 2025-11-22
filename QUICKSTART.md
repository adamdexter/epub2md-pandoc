# Quick Start Guide

## 1. Install Pandoc (One-time setup)

**macOS:**
```bash
brew install pandoc
```

**Ubuntu/Linux:**
```bash
sudo apt-get install pandoc
```

**Windows:**
Download installer from [pandoc.org/installing.html](https://pandoc.org/installing.html)

## 2. Verify Installation

```bash
pandoc --version
```

You should see version information. If not, restart your terminal.

## 3. Run the Script

```bash
# Basic usage (creates "md processed books" folder in current directory)
python3 epub_to_md_converter.py /path/to/your/epub/folder

# Example with your Downloads folder
python3 epub_to_md_converter.py ~/Downloads/Books

# Example with current directory
python3 epub_to_md_converter.py .
```

## 4. Find Your Converted Files

Look in the `md processed books` folder. Files will be named like:
- `Atomic Habits - James Clear (2018).md`
- `Deep Work - Cal Newport (2016).md`

## 5. Upload to Claude Projects

1. Open your Claude Project
2. Click "Add content" in Project Knowledge
3. Drag and drop your `.md` files
4. Done! Claude can now search and reference your books

## Example Workflow

```bash
# 1. Put all your EPUBs in a folder
mkdir ~/MyBooks

# 2. Run the converter
python3 epub_to_md_converter.py ~/MyBooks

# 3. Your converted books are now in "md processed books" folder
# 4. Upload them to Claude Projects!
```

## Filename Examples

The script creates AI-optimized filenames automatically:

**Before:** `atomic_habits_james_clear.epub`  
**After:** `Atomic Habits - James Clear (2018).md`

**Before:** `python-crash-course-2nd.epub`  
**After:** `Python Crash Course - Eric Matthes (2019) [2nd Edition].md`

This makes it easy for Claude (and you!) to identify and reference books.
