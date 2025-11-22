#!/usr/bin/env python3
"""
EPUB to Markdown Batch Converter
Converts all EPUB files in a folder to Markdown with AI-optimized filenames.
"""

import os
import subprocess
import re
import sys
from pathlib import Path
from typing import Optional, Tuple
import xml.etree.ElementTree as ET
import zipfile

def extract_epub_metadata(epub_path: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Extract metadata from EPUB file (title, author, year, edition).
    
    Returns:
        Tuple of (title, author, year, edition)
    """
    try:
        with zipfile.ZipFile(epub_path, 'r') as zip_ref:
            # Find the OPF file (metadata container)
            container_path = 'META-INF/container.xml'
            if container_path not in zip_ref.namelist():
                return None, None, None, None
            
            # Parse container.xml to find OPF file location
            container_content = zip_ref.read(container_path)
            container_root = ET.fromstring(container_content)
            
            # Find OPF file path
            ns = {'container': 'urn:oasis:names:tc:opendocument:xmlns:container'}
            rootfile = container_root.find('.//container:rootfile', ns)
            if rootfile is None:
                return None, None, None, None
            
            opf_path = rootfile.get('full-path')
            if opf_path not in zip_ref.namelist():
                return None, None, None, None
            
            # Parse OPF file for metadata
            opf_content = zip_ref.read(opf_path)
            opf_root = ET.fromstring(opf_content)
            
            # Define namespaces
            namespaces = {
                'opf': 'http://www.idpf.org/2007/opf',
                'dc': 'http://purl.org/dc/elements/1.1/'
            }
            
            # Extract metadata
            title = None
            author = None
            year = None
            edition = None
            
            # Get title
            title_elem = opf_root.find('.//dc:title', namespaces)
            if title_elem is not None and title_elem.text:
                title = title_elem.text.strip()
            
            # Get author (creator)
            author_elem = opf_root.find('.//dc:creator', namespaces)
            if author_elem is not None and author_elem.text:
                author = author_elem.text.strip()
            
            # Get date/year
            date_elem = opf_root.find('.//dc:date', namespaces)
            if date_elem is not None and date_elem.text:
                date_text = date_elem.text.strip()
                # Extract year from date (various formats)
                year_match = re.search(r'(\d{4})', date_text)
                if year_match:
                    year = year_match.group(1)
            
            # Try to find edition in various places
            # Check in title
            if title:
                edition_match = re.search(r'(\d+(?:st|nd|rd|th)\s+[Ee]dition|\d+\s+[Ee]d\.?)', title)
                if edition_match:
                    edition = edition_match.group(1)
            
            # Check in description
            desc_elem = opf_root.find('.//dc:description', namespaces)
            if desc_elem is not None and desc_elem.text and not edition:
                desc_text = desc_elem.text.strip()
                edition_match = re.search(r'(\d+(?:st|nd|rd|th)\s+[Ee]dition|\d+\s+[Ee]d\.?)', desc_text)
                if edition_match:
                    edition = edition_match.group(1)
            
            return title, author, year, edition
            
    except Exception as e:
        print(f"Warning: Could not extract metadata from {epub_path}: {e}")
        return None, None, None, None


def sanitize_filename(text: str) -> str:
    """
    Sanitize text for use in filename.
    Removes invalid characters and limits length.
    """
    if not text:
        return ""
    
    # Replace problematic characters
    text = re.sub(r'[<>:"/\\|?*]', '', text)
    # Replace multiple spaces/underscores with single space
    text = re.sub(r'[\s_]+', ' ', text)
    # Remove leading/trailing spaces
    text = text.strip()
    # Limit length
    if len(text) > 100:
        text = text[:100].rsplit(' ', 1)[0]  # Cut at word boundary
    
    return text


def create_ai_optimized_filename(title: Optional[str], author: Optional[str],
                                 year: Optional[str], edition: Optional[str],
                                 original_filename: str) -> str:
    """
    Create AI-optimized filename from metadata.
    Format: Title - Author Year Edition.md (no parentheses or brackets)
    """
    parts = []

    # Use original filename as fallback for title
    if not title:
        title = Path(original_filename).stem

    # Clean and add title
    clean_title = sanitize_filename(title)
    if clean_title:
        parts.append(clean_title)

    # Add author
    if author:
        clean_author = sanitize_filename(author)
        if clean_author:
            parts.append(f"- {clean_author}")

    # Add year (no parentheses)
    if year:
        parts.append(year)

    # Add edition (no brackets, convert to simple text)
    if edition:
        clean_edition = sanitize_filename(edition)
        if clean_edition:
            # Normalize edition format
            clean_edition = clean_edition.replace('Edition', 'Ed').replace('edition', 'Ed')
            parts.append(clean_edition)

    # Join parts and add extension
    filename = " ".join(parts) + ".md"

    # Final sanitization to remove any remaining special characters
    filename = filename.replace('(', '').replace(')', '').replace('[', '').replace(']', '')
    filename = sanitize_filename(filename.replace('.md', '')) + '.md'

    return filename


def check_pandoc_installed() -> bool:
    """Check if Pandoc is installed and accessible."""
    try:
        result = subprocess.run(['pandoc', '--version'], 
                              capture_output=True, 
                              text=True, 
                              check=False)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def clean_markdown_for_claude(content: str, title: Optional[str] = None,
                               author: Optional[str] = None,
                               year: Optional[str] = None) -> str:
    """
    Post-process markdown to optimize for Claude Project Knowledge.

    Removes:
    - Pandoc div artifacts (::: structures)
    - HTML anchor tags
    - Broken image references
    - Verbose list formatting
    - HTML blocks

    Adds:
    - Proper heading hierarchy
    - Metadata header
    - Clean formatting
    """
    import re

    # Add metadata header
    metadata = []
    if title or author or year:
        metadata.append("---")
        if title:
            metadata.append(f'title: "{title}"')
        if author:
            metadata.append(f'author: "{author}"')
        if year:
            metadata.append(f'year: {year}')
        metadata.append("---")
        metadata.append("")

    # Remove HTML anchor tags []{#id}
    content = re.sub(r'\[\]\{#[^}]+\}', '', content)

    # Remove Pandoc div structures (:::, ::::, etc.)
    content = re.sub(r'^:{3,}.*$', '', content, flags=re.MULTILINE)

    # Remove HTML div tags with IDs
    content = re.sub(r'<div[^>]*>.*?</div>', '', content, flags=re.DOTALL)

    # Remove HTML figure tags
    content = re.sub(r'<figure[^>]*>.*?</figure>', '[Image removed]', content, flags=re.DOTALL)

    # Remove or replace broken image references
    content = re.sub(r'!\[.*?\]\(\.\/images\/[^)]+\)', '[Image removed]', content)

    # Remove HTML comments
    content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)

    # Convert bold text at start of line to headings (likely chapter/section titles)
    # Match lines that are ONLY bold text (likely headings)
    def convert_bold_to_heading(match):
        text = match.group(1)
        # If it's all caps or title case and short, it's likely a heading
        if text.isupper() or (len(text) < 60 and text[0].isupper()):
            # Determine heading level based on length and style
            if text.isupper() and len(text) < 30:
                return f"# {text}"
            else:
                return f"## {text}"
        return match.group(0)  # Keep as bold if not heading-like

    content = re.sub(r'^\*\*([^\*]+)\*\*$', convert_bold_to_heading, content, flags=re.MULTILINE)

    # Clean up list formatting - remove verbose Pandoc list structures
    content = re.sub(r'^[ \t]*::: (?:ItemNumber|ItemContent|ClearBoth).*\n', '', content, flags=re.MULTILINE)
    content = re.sub(r'^[ \t]*:::[ \t]*\n', '', content, flags=re.MULTILINE)

    # Remove excessive blank lines (more than 2 consecutive)
    content = re.sub(r'\n{3,}', '\n\n', content)

    # Remove trailing whitespace from lines
    content = re.sub(r'[ \t]+$', '', content, flags=re.MULTILINE)

    # Clean up any remaining HTML tags (except for tables if needed)
    content = re.sub(r'<(?!table|tr|td|th|thead|tbody)[^>]+>', '', content)

    # Add metadata header if we have any
    if metadata:
        content = '\n'.join(metadata) + content

    return content


def convert_epub_to_md(epub_path: str, output_path: str,
                       title: Optional[str] = None,
                       author: Optional[str] = None,
                       year: Optional[str] = None) -> bool:
    """
    Convert EPUB to Markdown using Pandoc with Claude optimization.

    Args:
        epub_path: Path to input EPUB file
        output_path: Path to output Markdown file
        title: Book title for metadata
        author: Book author for metadata
        year: Publication year for metadata

    Returns:
        True if conversion successful, False otherwise
    """
    try:
        # Create output directory if it doesn't exist
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # Run Pandoc conversion with optimized settings
        cmd = [
            'pandoc',
            epub_path,
            '-o', output_path,
            '--markdown-headings=atx',      # Use # style headings
            '--wrap=none',                   # Don't wrap lines
            '--strip-comments',              # Remove HTML comments
            '--reference-links=false',       # Use inline links
            '--standalone',                  # Produce standalone document
        ]

        result = subprocess.run(cmd,
                              capture_output=True,
                              text=True,
                              check=False)

        if result.returncode != 0:
            print(f"  ❌ Pandoc error: {result.stderr}")
            return False

        # Post-process the markdown file for Claude optimization
        print(f"  🧹 Cleaning up markdown for Claude...")

        with open(output_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Apply Claude-specific optimizations
        cleaned_content = clean_markdown_for_claude(content, title, author, year)

        # Write cleaned content back
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(cleaned_content)

        # Report file size
        file_size = os.path.getsize(output_path)
        size_kb = file_size / 1024
        print(f"  📊 File size: {size_kb:.1f} KB")

        return True

    except Exception as e:
        print(f"  ❌ Conversion error: {e}")
        return False


def process_folder(input_folder: str, output_folder: str = "md processed books"):
    """
    Process all EPUB files in the input folder.
    
    Args:
        input_folder: Path to folder containing EPUB files
        output_folder: Path to output folder for Markdown files
    """
    # Check Pandoc installation
    if not check_pandoc_installed():
        print("❌ Error: Pandoc is not installed or not in PATH.")
        print("Please install Pandoc from: https://pandoc.org/installing.html")
        return
    
    # Get input folder path
    input_path = Path(input_folder)
    if not input_path.exists():
        print(f"❌ Error: Input folder '{input_folder}' does not exist.")
        return
    
    # Find all EPUB files
    epub_files = list(input_path.glob("*.epub"))
    
    if not epub_files:
        print(f"No EPUB files found in '{input_folder}'")
        return
    
    print(f"Found {len(epub_files)} EPUB file(s) to convert.\n")
    
    # Create output folder
    output_path = Path(output_folder)
    output_path.mkdir(exist_ok=True)
    
    # Process each EPUB file
    successful = 0
    failed = 0
    
    for i, epub_file in enumerate(epub_files, 1):
        print(f"[{i}/{len(epub_files)}] Processing: {epub_file.name}")
        
        # Extract metadata
        title, author, year, edition = extract_epub_metadata(str(epub_file))
        
        # Show extracted metadata
        if title:
            print(f"  📖 Title: {title}")
        if author:
            print(f"  ✍️  Author: {author}")
        if year:
            print(f"  📅 Year: {year}")
        if edition:
            print(f"  📚 Edition: {edition}")
        
        # Create optimized filename
        output_filename = create_ai_optimized_filename(
            title, author, year, edition, epub_file.name
        )
        output_file = output_path / output_filename
        
        print(f"  ➡️  Output: {output_filename}")

        # Convert file with metadata
        if convert_epub_to_md(str(epub_file), str(output_file), title, author, year):
            print(f"  ✅ Conversion successful!\n")
            successful += 1
        else:
            print(f"  ❌ Conversion failed!\n")
            failed += 1
    
    # Print summary
    print("=" * 60)
    print(f"Conversion complete!")
    print(f"✅ Successful: {successful}")
    if failed > 0:
        print(f"❌ Failed: {failed}")
    print(f"📁 Output folder: {output_path.absolute()}")


def main():
    """Main entry point for the script."""
    if len(sys.argv) < 2:
        print("Usage: python epub_to_md_converter.py <input_folder> [output_folder]")
        print("\nExample:")
        print("  python epub_to_md_converter.py ./books")
        print("  python epub_to_md_converter.py ./books ./converted")
        sys.exit(1)
    
    input_folder = sys.argv[1]
    output_folder = sys.argv[2] if len(sys.argv) > 2 else "md processed books"
    
    process_folder(input_folder, output_folder)


if __name__ == "__main__":
    main()
