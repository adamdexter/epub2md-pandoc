#!/usr/bin/env python3
"""
EPUB to Markdown Batch Converter
Converts all EPUB files in a folder to Markdown with AI-optimized filenames.
"""

import os
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from version import __version__ as CONVERTER_VERSION

# EPUB Quality Pre-Check Configuration
EPUB_QUALITY_THRESHOLD = 70.0  # Minimum quality score (0-100)
SKIP_LOW_QUALITY_EPUBS = True  # Set to False to disable pre-check
ALLOW_QUALITY_OVERRIDE = True  # Allow user to override skip decision

def extract_epub_metadata(epub_path: str) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
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


def build_toc_anchor_map(content: str) -> dict:
    """
    Parse TOC-style links in Pandoc EPUB output and build {anchor_id: heading_text}.

    Many EPUBs ship with chapter titles only in a TOC of `[**Chapter X**...](#anchor)`
    links and no real <h1>/<h2> tags in chapter bodies. Pandoc therefore emits
    zero `#` headings. This builder lets us reconstruct headings from the TOC.
    """
    mapping = {}

    # Inner alternation: a non-bracket char OR a single nested [..] pair.
    # The branches don't overlap (the second consumes a balanced `[..]` and
    # nothing else), which keeps backtracking linear on pathological input.
    link_pattern = re.compile(
        r'\[((?:[^\[\]]|\[[^\]]*\])+)\]\(#([^)\s]+)\)',
        re.DOTALL
    )

    for m in link_pattern.finditer(content):
        link_text = m.group(1)
        anchor = m.group(2)

        if anchor in mapping:
            continue

        title_m = re.search(r'\*{2,3}([^\*\n]+?)\*{2,3}', link_text)
        if not title_m:
            continue

        title = title_m.group(1).strip(' *\\')
        if not title or len(title) > 120:
            continue

        subtitle_m = re.search(r'\[([^\]\n]+?)\]\{\.\w+\}', link_text)
        subtitle = subtitle_m.group(1).strip(' *\\') if subtitle_m else None

        if subtitle and subtitle.lower() != title.lower() and len(subtitle) <= 120:
            heading = f"{title}: {subtitle}"
        else:
            heading = title

        mapping[anchor] = heading

    return mapping


def apply_toc_anchor_headings(content: str, anchor_map: dict) -> tuple[str, int]:
    """Insert `# heading` lines at each `[]{#anchor}` marker found in body."""
    if not anchor_map:
        return content, 0

    conversions = 0

    def replace(m):
        nonlocal conversions
        anchor = m.group(1)
        if anchor in anchor_map:
            conversions += 1
            return f"# {anchor_map[anchor]}\n\n{m.group(0)}"
        return m.group(0)

    new_content = re.sub(
        r'^\[\]\{#([^}\s]+)\}\s*$',
        replace,
        content,
        flags=re.MULTILINE
    )

    return new_content, conversions


def analyze_artifacts(content: str) -> dict:
    """
    Analyze markdown content for various artifact types.

    Returns:
        Dictionary with artifact counts and line count
    """
    lines = content.split('\n')
    line_count = len(lines)

    artifacts = {
        'line_count': line_count,
        'header_ids': len(re.findall(r'^#{1,6}\s+.*\{#[^}]*\}', content, re.MULTILINE)),
        'html_blocks': content.count('``{=html}'),
        'citations': len(re.findall(r'\[\[.*?\]\(#[^)]*\)\{\.biblioref[^}]*\}', content)),
        'image_attrs': len(re.findall(r'!\[.*?\]\(.*?\)\{[^}]+\}', content)),
        'bracket_classes': len(re.findall(r'\[[^\]]+\]\{[^}]+\}', content)),
        'xhtml_links': len(re.findall(r'\[.*?\]\(#\d+_[^)]*\.xhtml[^)]*\)', content)),
        'blockquote_divs': len(re.findall(r'^> ::: \{\}$', content, re.MULTILINE))
    }

    return artifacts


def assess_epub_quality(epub_path: str) -> dict:
    """
    Pre-conversion EPUB quality assessment.

    Runs a quick Pandoc conversion to temporary file and analyzes structure
    and artifacts to predict final quality before full conversion.

    Returns:
        dict with:
            - score: float (0-100)
            - issues: list of str (detected problems)
            - recommendation: str ('proceed', 'skip', 'review')
            - details: dict (detailed metrics)
    """
    import os
    import subprocess
    import tempfile

    # Create temporary file for test conversion
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # Quick Pandoc conversion (minimal options for speed)
        cmd = [
            'pandoc',
            str(epub_path),
            '-o', tmp_path,
            '--to=markdown',
            '--wrap=none'
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)

        if result.returncode != 0:
            return {
                'score': 0.0,
                'issues': ['Pandoc conversion failed'],
                'recommendation': 'skip',
                'details': {'error': result.stderr}
            }

        # Read and analyze the temporary conversion
        with open(tmp_path, encoding='utf-8') as f:
            content = f.read()

        # Analyze structure and artifacts
        lines = content.split('\n')
        line_count = len(lines)

        # Count potential issues
        heading_count = len([line for line in lines if line.strip().startswith('#')])
        html_blocks = content.count('``{=html}')

        # Check for styled text that should be headings (HeART book pattern)
        calibre_markers = len([line for line in lines if '{.calibre' in line and line.strip().startswith('[')])

        # Check for ALL-CAPS lines (potential undetected headings)
        caps_lines = len([line for line in lines if line.strip() and len(line.strip()) > 10 and
                         line.strip().isupper() and not line.strip().startswith('#')])

        # Check for TOC-anchored chapter pattern (Sway-style EPUBs):
        # `[**Chapter X**...](#anchor)` links in TOC + `[]{#anchor}` markers in body
        toc_anchor_map = build_toc_anchor_map(content)
        anchor_markers = len(re.findall(r'^\[\]\{#[^}\s]+\}\s*$', content, re.MULTILINE))
        toc_anchor_fixable = sum(
            1 for a in toc_anchor_map if anchor_markers and a in content
        )

        # Count artifacts
        header_attrs = len([line for line in lines if line.strip().startswith('#') and '{' in line])
        role_attrs = content.count('role=')
        bracket_classes = len(re.findall(r'\[[^\]]+\]\{[^}]+\}', content))

        # Calculate metrics
        issues = []
        details = {
            'lines': line_count,
            'headings': heading_count,
            'html_blocks': html_blocks,
            'calibre_markers': calibre_markers,
            'caps_lines': caps_lines,
            'header_attrs': header_attrs,
            'role_attrs': role_attrs,
            'bracket_classes': bracket_classes,
            'toc_anchor_fixable': toc_anchor_fixable
        }

        # Assess quality
        score = 100.0

        # Issue 1: Missing headings (critical - structure problem)
        if line_count > 1000 and heading_count == 0:
            # Check if this is AUTO-FIXABLE (has Calibre markers)
            if calibre_markers > 50:
                # This will be automatically fixed during conversion - lower penalty
                issues.append(f"Fixable: {calibre_markers} Calibre-style markers detected (will auto-convert to headings)")
                score -= 15.0  # Lower penalty - converter handles this automatically
            elif toc_anchor_fixable >= 3:
                issues.append(f"Fixable: {toc_anchor_fixable} TOC-anchored sections detected (will auto-convert to headings)")
                score -= 15.0
            elif caps_lines > 30:
                # ALL-CAPS pattern - not auto-fixable, but detectable
                issues.append(f"WARNING: {caps_lines} ALL-CAPS lines - possible undetected headings (not auto-fixable)")
                score -= 30.0
            else:
                # No detectable patterns - critical issue
                issues.append(f"CRITICAL: Zero headings detected in {line_count} lines")
                score -= 40.0
        elif line_count > 1000 and heading_count < 10:
            issues.append(f"WARNING: Only {heading_count} headings in {line_count} lines")
            score -= 20.0

        # Issue 2: Heavy inline HTML artifacts (Coaching book pattern)
        if html_blocks > 200:
            issues.append(f"MAJOR: {html_blocks} HTML blocks detected")
            score -= 25.0
        elif html_blocks > 100:
            issues.append(f"WARNING: {html_blocks} HTML blocks")
            score -= 15.0

        # Issue 3: Heavy role attributes
        if role_attrs > 50:
            issues.append(f"WARNING: {role_attrs} role attributes")
            score -= 10.0

        # Issue 4: Heavy bracket classes
        if bracket_classes > 500:
            issues.append(f"WARNING: {bracket_classes} bracket classes")
            score -= 10.0

        score = max(0.0, score)

        # Make recommendation
        if score >= 80:
            recommendation = 'proceed'
        elif score >= EPUB_QUALITY_THRESHOLD:
            recommendation = 'proceed'
        else:
            recommendation = 'skip'

        return {
            'score': score,
            'issues': issues,
            'recommendation': recommendation,
            'details': details
        }

    except subprocess.TimeoutExpired:
        return {
            'score': 0.0,
            'issues': ['Conversion timeout - file may be corrupt'],
            'recommendation': 'skip',
            'details': {}
        }
    except Exception as e:
        return {
            'score': 0.0,
            'issues': [f'Assessment error: {str(e)}'],
            'recommendation': 'skip',
            'details': {}
        }
    finally:
        # Clean up temp file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def calculate_optimization_score(artifacts: dict) -> float:
    """
    Calculate optimization score based on artifact density.

    Score starts at 100% and is reduced based on artifact density per 1000 lines.

    Returns:
        Optimization score (0-100)
    """
    line_count = artifacts['line_count']
    if line_count == 0:
        return 100.0

    score = 100.0
    density_factor = line_count / 1000.0

    # Apply deductions based on artifact density
    score -= (artifacts['header_ids'] / density_factor) * 0.5
    score -= (artifacts['html_blocks'] / density_factor) * 2.0
    score -= (artifacts['citations'] / density_factor) * 0.2
    score -= (artifacts['image_attrs'] / density_factor) * 0.1
    score -= (artifacts['bracket_classes'] / density_factor) * 0.3
    score -= (artifacts['xhtml_links'] / density_factor) * 0.1
    score -= (artifacts['blockquote_divs'] / density_factor) * 0.05

    return max(0.0, score)


def collect_quality_signals(epub_path: str, md_path: str) -> dict:
    """
    Gather the converter's deterministic quality signals for a converted file.

    Reuses the existing scoring functions so the LLM judge and the regression
    tests share one oracle. Returns a JSON-serializable dict.

    Args:
        epub_path: Path to the original EPUB.
        md_path: Path to the produced Markdown file.
    """
    with open(md_path, encoding='utf-8') as f:
        md_content = f.read()

    artifacts = analyze_artifacts(md_content)
    optimization_score = calculate_optimization_score(artifacts)
    heading_count = len(re.findall(r'^#{1,6}\s+', md_content, re.MULTILINE))

    try:
        epub_quality = assess_epub_quality(epub_path)
    except Exception as e:  # pre-check is best-effort; never block on it
        epub_quality = {
            'score': None,
            'issues': [f'assessment error: {e}'],
            'recommendation': 'unknown',
            'details': {},
        }

    return {
        'optimization_score': round(optimization_score, 2),
        'artifacts': artifacts,
        'line_count': artifacts['line_count'],
        'heading_count': heading_count,
        'md_char_count': len(md_content),
        'epub_quality': epub_quality,
    }


def apply_aggressive_cleanup(content: str, artifacts: dict, verbose: bool = False) -> str:
    """
    Apply aggressive cleanup operations for suboptimal EPUBs.

    This runs additional cleanup beyond the basic operations when
    the optimization score is below 85%.

    Args:
        content: Markdown content
        artifacts: Artifact analysis results
        verbose: Print detailed logging

    Returns:
        Cleaned content
    """
    operations_run = []

    # Priority 1: Remove ALL header attributes (FIXED - handle Pandoc patterns)
    header_attrs_before = len(re.findall(r'^#{1,6}\s+.*\{', content, re.MULTILINE))
    if header_attrs_before > 0:
        # Handle Pandoc's []{#anchor}[Text]{.class} pattern first
        # Convert: # []{#anchor}[Text]{.class} → # [Text]{.class}
        content = re.sub(
            r'^(#{1,6})\s+\[\]\{[^}]*\}(.+)$',
            r'\1 \2',
            content,
            flags=re.MULTILINE
        )

        # Then remove all remaining {attribute} patterns
        content = re.sub(r'^(#{1,6}\s+.+?)\s*\{[^}]*\}.*$', r'\1', content, flags=re.MULTILINE)

        header_attrs_after = len(re.findall(r'^#{1,6}\s+.*\{', content, re.MULTILINE))
        if verbose and header_attrs_before > header_attrs_after:
            print(f"       → Removed {header_attrs_before - header_attrs_after} header attributes")
        operations_run.append(f"header_attrs: {header_attrs_before} → {header_attrs_after}")

    # Priority 2: Remove HTML comment blocks (BRUTE FORCE - string matching)
    html_block_count = content.count('``{=html}')
    if html_block_count > 0:
        lines = content.split('\n')
        cleaned_lines = []
        removed = 0

        for line in lines:
            if line.strip() == '``{=html}':
                removed += 1
                continue  # Skip HTML block lines
            cleaned_lines.append(line)

        content = '\n'.join(cleaned_lines)

        if verbose:
            print(f"       → Removed {removed} HTML blocks")
        operations_run.append(f"html_blocks: {html_block_count} → 0")

    # Priority 3: Simplify citation references
    if artifacts['citations'] > 0:
        before = len(re.findall(r'\[\[.*?\]\(#[^)]*\)\{\.biblioref[^}]*\}', content))
        content = re.sub(r'\[\[([^\]]*)\]\(#[^)]*\)\{[^}]*\}\]', r'[\1]', content)
        after = len(re.findall(r'\[\[.*?\]\(#[^)]*\)\{\.biblioref[^}]*\}', content))
        if verbose and before > after:
            print(f"       → Simplified {before - after} citations")
        operations_run.append(f"citations: {before} → {after}")

    # Priority 4: Remove image attributes
    if artifacts['image_attrs'] > 0:
        before = len(re.findall(r'!\[.*?\]\(.*?\)\{[^}]+\}', content))
        content = re.sub(r'(!\[[^\]]*\]\([^)]*\))\{[^}]*\}', r'\1', content)
        after = len(re.findall(r'!\[.*?\]\(.*?\)\{[^}]+\}', content))
        if verbose and before > after:
            print(f"       → Cleaned {before - after} image attributes")
        operations_run.append(f"image_attrs: {before} → {after}")

    # Priority 5: Remove bracketed text classes
    if artifacts['bracket_classes'] > 0:
        before = len(re.findall(r'\[[^\]]+\]\{[^}]+\}', content))
        content = re.sub(r'(\[[^\]]+\])\{[^}]+\}', r'\1', content)
        after = len(re.findall(r'\[[^\]]+\]\{[^}]+\}', content))
        if verbose and before > after:
            print(f"       → Cleaned {before - after} bracket classes")
        operations_run.append(f"bracket_classes: {before} → {after}")

    # Priority 6: Clean internal XHTML links
    if artifacts['xhtml_links'] > 0:
        before = len(re.findall(r'\[.*?\]\(#\d+_[^)]*\.xhtml[^)]*\)', content))
        content = re.sub(r'\[([^\]]*)\]\(#\d+_[^)]*\.xhtml[^)]*\)', r'[\1]', content)
        after = len(re.findall(r'\[.*?\]\(#\d+_[^)]*\.xhtml[^)]*\)', content))
        if verbose and before > after:
            print(f"       → Cleaned {before - after} XHTML links")
        operations_run.append(f"xhtml_links: {before} → {after}")

    # Priority 7: Clean blockquote divs
    if artifacts['blockquote_divs'] > 0:
        before = len(re.findall(r'^> ::: \{\}$', content, re.MULTILINE))
        content = re.sub(r'^> ::: \{\}$', '', content, flags=re.MULTILINE)
        content = re.sub(r'^> :::$', '', content, flags=re.MULTILINE)
        after = len(re.findall(r'^> ::: \{\}$', content, re.MULTILINE))
        if verbose and before > after:
            print(f"       → Cleaned {before - after} blockquote divs")
        operations_run.append(f"blockquote_divs: {before} → {after}")

    # Priority 8: Remove ghost headers (NEW - was missing)
    ghost_headers_before = len(re.findall(r'^#{1,6}\s*\[\]\s*$', content, re.MULTILINE))
    if ghost_headers_before > 0:
        content = re.sub(r'^#{1,6}\s*\[\]\s*$', '', content, flags=re.MULTILINE)
        ghost_headers_after = len(re.findall(r'^#{1,6}\s*\[\]\s*$', content, re.MULTILINE))
        if verbose and ghost_headers_before > ghost_headers_after:
            print(f"       → Removed {ghost_headers_before - ghost_headers_after} ghost headers")
        operations_run.append(f"ghost_headers: {ghost_headers_before} → {ghost_headers_after}")

    # Priority 9: Clean endnote/footnote references (NEW - was missing)
    endnote_before = len(re.findall(r'\[\\\[\d+\\\]\]\([^)]*\)\{[^}]*\}', content))
    if endnote_before > 0:
        content = re.sub(
            r'\[\\\[(\d+)\\\]\]\([^)]*\)\{[^}]*\}',
            r'[\1]',
            content
        )
        endnote_after = len(re.findall(r'\[\\\[\d+\\\]\]\([^)]*\)\{[^}]*\}', content))
        if verbose and endnote_before > endnote_after:
            print(f"       → Cleaned {endnote_before - endnote_after} endnote references")
        operations_run.append(f"endnotes: {endnote_before} → {endnote_after}")

    # Priority 10: Clean bracketed section numbers in headers (NEW - was missing)
    before_section_nums = len(re.findall(r'^#{1,6}\s*\[[\d.]+\s*\]', content, re.MULTILINE))
    if before_section_nums > 0:
        content = re.sub(r'^(#{1,6}\s*)\[([\d.]+)\s*\]', r'\1\2. ', content, flags=re.MULTILINE)
        if verbose:
            print(f"       → Cleaned {before_section_nums} section number brackets")

    # Clean up multiple consecutive blank lines (can accumulate from removals)
    content = re.sub(r'\n{3,}', '\n\n', content)

    return content


def add_metadata_only(content: str, title: Optional[str] = None,
                      author: Optional[str] = None,
                      year: Optional[str] = None) -> str:
    """
    Add only YAML metadata header to already-optimal files.

    This function is used when a file has a high optimization score (≥ 85%)
    and doesn't need cleanup. It preserves the file exactly as-is and only
    adds metadata.

    Args:
        content: Original markdown content
        title: Book title for metadata
        author: Book author for metadata
        year: Publication year for metadata

    Returns:
        Content with metadata header prepended
    """
    # Build metadata header
    metadata = []
    if title or author or year:
        metadata.append("---")
        if title:
            metadata.append(f'title: "{title}"')
        if author:
            metadata.append(f'author: "{author}"')
        if year:
            metadata.append(f'year: {year}')

        # Add version tracking (invisible to Claude Projects)
        metadata.append(f'converter_version: "{CONVERTER_VERSION}"')
        metadata.append(f'processed_date: "{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}"')

        metadata.append("---")
        metadata.append("")

        return '\n'.join(metadata) + content

    # No metadata to add, return as-is
    return content


def clean_markdown_for_claude(content: str, title: Optional[str] = None,
                               author: Optional[str] = None,
                               year: Optional[str] = None) -> str:
    """
    Post-process markdown to optimize for Claude Project Knowledge.

    Removes:
    - Page navigation sections (CRITICAL - wastes 10K+ tokens)
    - Pandoc div artifacts (::: structures)
    - HTML anchor tags
    - Class annotations {.className}
    - Broken image references
    - Verbose list formatting
    - HTML blocks
    - Escaped apostrophes and quotes

    Adds:
    - Proper heading hierarchy
    - Metadata header
    - Clean formatting
    """
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

        # Add version tracking (invisible to Claude Projects)
        metadata.append(f'converter_version: "{CONVERTER_VERSION}"')
        metadata.append(f'processed_date: "{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}"')

        metadata.append("---")
        metadata.append("")

    # CRITICAL: Remove page navigation sections (can waste 10,000+ tokens!)
    # Matches sections like:
    # ## Pages
    # 1. [i](#page_i)
    # 2. [ii](#page_ii)
    # ... hundreds of lines ...
    # This regex finds "## Pages" or "## Guide" through the next heading
    content = re.sub(
        r'^##\s+(Pages|Guide|Landmarks)\s*\n\n(?:[\s\S]*?)(?=^#[^#]|\Z)',
        '',
        content,
        flags=re.MULTILINE
    )

    # Remove class annotations from headings and text: {.className}
    # Example: # [Foreword]{.chapterTitle} → # Foreword
    content = re.sub(r'\{\.[\w-]+\}', '', content)

    # Remove bracket wrappers around heading text
    # Example: # [Introduction] → # Introduction
    content = re.sub(r'^(#{1,6})\s+\[([^\]]+)\]\s*$', r'\1 \2', content, flags=re.MULTILINE)

    # Fix escaped apostrophes and quotes
    content = content.replace("\\'", "'")
    content = content.replace('\\"', '"')
    content = content.replace('\\&', '&')

    # Remove HTML anchor tags []{#id}
    content = re.sub(r'\[\]\{#[^}]+\}', '', content)

    # Remove inline anchor references like {#id}
    content = re.sub(r'\{#[\w-]+\}', '', content)

    # Remove Pandoc div structures (:::, ::::, etc.)
    content = re.sub(r'^:{3,}.*$', '', content, flags=re.MULTILINE)

    # Remove HTML div tags with IDs
    content = re.sub(r'<div[^>]*>.*?</div>', '', content, flags=re.DOTALL)

    # Remove HTML figure tags
    content = re.sub(r'<figure[^>]*>.*?</figure>', '[Image removed]', content, flags=re.DOTALL)

    # Remove or replace broken image references (multiple patterns)
    content = re.sub(r'!\[.*?\]\(\.?\/images\/[^)]+\)', '[Image removed]', content)
    content = re.sub(r'!\[\]\([^)]*\.(jpg|jpeg|png|gif|svg)\)', '[Image removed]', content, flags=re.IGNORECASE)

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

    # Remove "booksection" and similar class wrappers
    content = re.sub(r'^[ \t]*::: (?:booksection|section|chapter).*\n', '', content, flags=re.MULTILINE)

    # ALWAYS remove HTML blocks (regardless of optimization score)
    # This ensures HTML blocks are removed even when file scores > 85%
    if '``{=html}' in content:
        lines = content.split('\n')
        cleaned_lines = []
        removed_count = 0

        for line in lines:
            if line.strip() == '``{=html}':
                removed_count += 1
                continue
            cleaned_lines.append(line)

        content = '\n'.join(cleaned_lines)

    # Remove excessive blank lines (more than 2 consecutive)
    content = re.sub(r'\n{3,}', '\n\n', content)

    # Remove trailing whitespace from lines
    content = re.sub(r'[ \t]+$', '', content, flags=re.MULTILINE)

    # Clean up any remaining HTML tags (except for tables if needed)
    content = re.sub(r'<(?!table|tr|td|th|thead|tbody)[^>]+>', '', content)

    # Remove empty headings (headings with no text)
    content = re.sub(r'^#{1,6}\s*$', '', content, flags=re.MULTILINE)

    # Final cleanup: remove more than 2 consecutive blank lines
    content = re.sub(r'\n{3,}', '\n\n', content)

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
    # ========================================================================
    # EPUB QUALITY PRE-CHECK (NEW - DO NOT MODIFY EXISTING CODE BELOW)
    # ========================================================================

    if SKIP_LOW_QUALITY_EPUBS:
        print("  🔍 Running quality pre-check...")
        assessment = assess_epub_quality(epub_path)

        score = assessment['score']
        issues = assessment['issues']
        recommendation = assessment['recommendation']

        print(f"     Quality Score: {score:.1f}% (threshold: {EPUB_QUALITY_THRESHOLD}%)")

        if issues:
            print("     Issues detected:")
            for issue in issues:
                print(f"       • {issue}")

        if recommendation == 'skip':
            print("  ⚠️  QUALITY BELOW THRESHOLD - SKIPPING")
            print("     Detected issues:")
            for issue in issues:
                print(f"       • {issue}")

            # Show detailed metrics
            details = assessment['details']
            if details.get('calibre_markers', 0) > 50:
                print("     💡 Tip: This EPUB may need heading conversion script")
                print(f"        ({details['calibre_markers']} Calibre-style markers found)")

            if ALLOW_QUALITY_OVERRIDE:
                print("\n     To process anyway, set SKIP_LOW_QUALITY_EPUBS = False")
                print(f"     or adjust EPUB_QUALITY_THRESHOLD (current: {EPUB_QUALITY_THRESHOLD}%)")

            return False  # Skip this file

        elif recommendation == 'proceed':
            if issues:
                print("     ⚠️  Issues detected but above threshold - proceeding")
            else:
                print("     ✓ Quality check passed")

        print()  # Blank line before conversion starts

    # ========================================================================
    # EXISTING CONVERSION CODE CONTINUES HERE (UNCHANGED)
    # ========================================================================

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
            # (inline links are pandoc's default; the old `--reference-links=false`
            #  is rejected by older pandoc — e.g. CI's — so we just omit it)
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
        print("  🔍 Analyzing artifacts...")

        with open(output_path, encoding='utf-8') as f:
            original_content = f.read()

        original_size = len(original_content)

        # ====================================================================
        # AUTO-CONVERT CALIBRE-STYLE HEADINGS (BEFORE ARTIFACT ANALYSIS)
        # ====================================================================
        # Some EPUBs use [TEXT]{.calibreX} instead of markdown headings.
        # Convert these EARLY so they're counted as proper headings in scoring.

        if '{.calibre' in original_content:
            lines = original_content.split('\n')
            converted_lines = []
            calibre_conversions = 0

            for line in lines:
                # Pattern: [**TEXT**]{.calibreX} or [TEXT]{.calibreX}
                match = re.match(r'^\[(\*\*)?(.*?)(\*\*)?\]\{\.calibre\d+\}(.*)$', line)

                if match:
                    has_bold = bool(match.group(1))
                    text = match.group(2).strip()
                    trailing = match.group(4)

                    # Skip empty or punctuation-only text
                    if not text or text in [':', '-', '*', '**', '  ']:
                        converted_lines.append(line)
                        continue

                    # Determine heading level
                    if 'CHAPTER' in text.upper() and re.match(r'.*CHAPTER\s+\d+', text.upper()):
                        line = f'# {text}{trailing}'
                        calibre_conversions += 1
                    elif 'PART' in text.upper() and re.match(r'.*PART\s+[IVX0-9]+', text.upper()):
                        line = f'# {text}{trailing}'
                        calibre_conversions += 1
                    elif text.upper() in [
                        'DEDICATION', 'INTRODUCTION', 'CONCLUSION', 'GLOSSARY',
                        'REFERENCES', 'RESOURCES', 'APPENDIX', 'FOREWORD',
                        'PREFACE', 'ACKNOWLEDGMENTS', 'ABOUT THE AUTHOR',
                        'TABLE OF CONTENTS', 'INDEX'
                    ]:
                        line = f'# {text}{trailing}'
                        calibre_conversions += 1
                    elif has_bold and len(text) > 35:
                        line = f'## {text}{trailing}'
                        calibre_conversions += 1
                    elif has_bold and len(text) > 20:
                        line = f'### {text}{trailing}'
                        calibre_conversions += 1
                    elif has_bold:
                        line = f'### {text}{trailing}'
                        calibre_conversions += 1
                    else:
                        line = f'#### {text}{trailing}'
                        calibre_conversions += 1

                converted_lines.append(line)

            if calibre_conversions > 0:
                original_content = '\n'.join(converted_lines)
                print(f"     → Auto-converted {calibre_conversions} Calibre-style headings to markdown")

        # ====================================================================
        # AUTO-CONVERT TOC-ANCHORED CHAPTERS (Sway-style EPUBs)
        # ====================================================================
        # Some EPUBs have no real headings — chapter titles only exist as TOC
        # links pointing to `[]{#anchor}` markers in the body. Reconstruct
        # headings by mapping TOC anchors to titles and inserting at markers.

        if re.search(r'^\[\]\{#[^}\s]+\}\s*$', original_content, re.MULTILINE):
            anchor_map = build_toc_anchor_map(original_content)
            original_content, toc_conversions = apply_toc_anchor_headings(
                original_content, anchor_map
            )
            if toc_conversions > 0:
                print(f"     → Auto-converted {toc_conversions} TOC-anchored sections to headings")

        # ====================================================================
        # CONTINUE WITH EXISTING FLOW (UNCHANGED)
        # ====================================================================

        # Phase 1: Analyze artifacts
        artifacts = analyze_artifacts(original_content)
        score = calculate_optimization_score(artifacts)

        # Report artifact analysis
        total_artifacts = sum([artifacts[k] for k in artifacts.keys() if k != 'line_count'])

        print(f"  📋 Total artifacts found: {total_artifacts}")
        if total_artifacts > 0:
            print("     Details:")
            if artifacts['header_ids'] > 0:
                print(f"       • Header IDs: {artifacts['header_ids']}")
            if artifacts['html_blocks'] > 0:
                print(f"       • HTML blocks: {artifacts['html_blocks']}")
            if artifacts['citations'] > 0:
                print(f"       • Citations: {artifacts['citations']}")
            if artifacts['image_attrs'] > 0:
                print(f"       • Image attributes: {artifacts['image_attrs']}")
            if artifacts['bracket_classes'] > 0:
                print(f"       • Bracket classes: {artifacts['bracket_classes']}")
            if artifacts['xhtml_links'] > 0:
                print(f"       • XHTML links: {artifacts['xhtml_links']}")
            if artifacts['blockquote_divs'] > 0:
                print(f"       • Blockquote divs: {artifacts['blockquote_divs']}")

        print(f"  📈 Optimization score: {score:.1f}%")
        print(f"  🎯 Threshold: 85% - {'SKIP cleanup' if score >= 85.0 else 'RUN cleanup'}")

        # Phase 2: Conditional cleanup with 85% threshold
        if score < 85.0:
            print("  🧹 Running aggressive cleanup (score < 85%)...")
            print("     Step 1: Applying aggressive artifact removal...")

            # First apply aggressive cleanup for suboptimal EPUBs
            cleaned_content = apply_aggressive_cleanup(original_content, artifacts, verbose=True)

            # Check what aggressive cleanup did
            mid_size = len(cleaned_content)
            mid_reduction = ((original_size - mid_size) / original_size * 100) if original_size > 0 else 0
            print(f"     Step 1 complete: {mid_reduction:.1f}% reduction")

            print("     Step 2: Applying standard Claude optimizations...")
            # Then apply standard Claude optimizations
            cleaned_content = clean_markdown_for_claude(cleaned_content, title, author, year)

            # Re-analyze to show improvement
            post_artifacts = analyze_artifacts(cleaned_content)
            post_score = calculate_optimization_score(post_artifacts)
            post_total = sum([post_artifacts[k] for k in post_artifacts.keys() if k != 'line_count'])

            print(f"  ✨ Post-cleanup score: {post_score:.1f}%")
            print(f"  📉 Artifacts remaining: {post_total} (removed {total_artifacts - post_total})")
        else:
            print("  ✅ File already optimal (score ≥ 85%)")
            print("  ⏭️  Skipping all cleanup operations")
            print("  📝 Adding metadata header only...")
            # File is already clean - only add metadata, don't run cleanup
            cleaned_content = add_metadata_only(original_content, title, author, year)

        # Write cleaned content back
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(cleaned_content)

        # Report statistics
        cleaned_size = len(cleaned_content)
        reduction = ((original_size - cleaned_size) / original_size * 100) if original_size > 0 else 0

        file_size_kb = cleaned_size / 1024

        # Count headings for quality check
        heading_count = len(re.findall(r'^#{1,6}\s+', cleaned_content, re.MULTILINE))

        print(f"  📊 File size: {file_size_kb:.1f} KB")
        if reduction > 0:
            print(f"  🎯 Reduced by: {reduction:.1f}%")
        print(f"  📑 Headings found: {heading_count}")
        print("  🎉 Ready for Claude Projects!")

        return True

    except Exception as e:
        print(f"  ❌ Conversion error: {e}")
        return False


def process_folder(input_folder: str, output_folder: str = "md processed books") -> list:
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
        return []
    
    # Get input folder path
    input_path = Path(input_folder)
    if not input_path.exists():
        print(f"❌ Error: Input folder '{input_folder}' does not exist.")
        return []
    
    # Find all EPUB files
    epub_files = list(input_path.glob("*.epub"))
    
    if not epub_files:
        print(f"No EPUB files found in '{input_folder}'")
        return []
    
    print(f"Found {len(epub_files)} EPUB file(s) to convert.\n")
    
    # Create output folder
    output_path = Path(output_folder)
    output_path.mkdir(exist_ok=True)
    
    # Process each EPUB file
    successful = 0
    failed = 0
    converted_pairs = []  # (epub_path, md_path) for each successful conversion
    
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
            print("  ✅ Conversion successful!\n")
            successful += 1
            converted_pairs.append((str(epub_file), str(output_file)))
        else:
            print("  ❌ Conversion failed!\n")
            failed += 1
    
    # Print summary
    print("=" * 60)
    print("Conversion complete!")
    print(f"✅ Successful: {successful}")
    if failed > 0:
        print(f"❌ Failed: {failed}")
    print(f"📁 Output folder: {output_path.absolute()}")

    return converted_pairs


def main():
    """Main entry point for the script."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Convert EPUB files to AI-optimized Markdown',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python epub_to_md_converter.py ./books
  python epub_to_md_converter.py ./books ./converted
  python epub_to_md_converter.py ./books --rag --rag-quality max
        """
    )

    parser.add_argument('input_folder', help='Folder containing EPUB files to convert')
    parser.add_argument('output_folder', nargs='?', default='md processed books',
                        help='Output folder for Markdown files (default: "md processed books")')
    parser.add_argument('--rag', action='store_true',
                        help='Generate RAG-optimized .rag.md companion (Gemini API)')
    parser.add_argument('--rag-quality', choices=['standard', 'max'], default='standard',
                        help='Distillation quality tier (default: standard)')
    parser.add_argument('--rag-accuracy-critical', action='store_true',
                        help='Copy tables/figures verbatim and verify every numeral in the companion')

    args = parser.parse_args()

    pairs = process_folder(args.input_folder, args.output_folder)

    if args.rag and pairs:
        try:
            import rag_distill  # lazy — only imported when --rag is set
            for _src, md in pairs:
                rag_distill.distill_markdown(md, quality=args.rag_quality,
                                             accuracy_critical=args.rag_accuracy_critical,
                                             source_kind='epub')
                # distill_markdown never raises; its log lines print via default log=print
        except Exception as e:
            print(f"RAG distill error (conversion unaffected): {e}")


if __name__ == "__main__":
    main()
