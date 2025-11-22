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
    Format: Title - Author (Year) [Edition].md
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
    
    # Add year in parentheses
    if year:
        parts.append(f"({year})")
    
    # Add edition in brackets
    if edition:
        clean_edition = sanitize_filename(edition)
        if clean_edition:
            parts.append(f"[{clean_edition}]")
    
    # Join parts and add extension
    filename = " ".join(parts) + ".md"
    
    # Final sanitization
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


def convert_epub_to_md(epub_path: str, output_path: str) -> bool:
    """
    Convert EPUB to Markdown using Pandoc.
    
    Args:
        epub_path: Path to input EPUB file
        output_path: Path to output Markdown file
        
    Returns:
        True if conversion successful, False otherwise
    """
    try:
        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Run Pandoc conversion
        cmd = [
            'pandoc',
            epub_path,
            '-o', output_path,
            '--markdown-headings=atx',  # Use # style headings
            '--wrap=none',  # Don't wrap lines
            '--extract-media=.',  # Extract images to current directory
        ]
        
        result = subprocess.run(cmd, 
                              capture_output=True, 
                              text=True, 
                              check=False)
        
        if result.returncode != 0:
            print(f"  ❌ Pandoc error: {result.stderr}")
            return False
        
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
        
        # Convert file
        if convert_epub_to_md(str(epub_file), str(output_file)):
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
