#!/usr/bin/env python3
"""
PDF to Markdown Converter (pdf2md)
==================================
Converts PDF documents (books, whitepapers, presentations, scanned documents)
to AI-optimized Markdown for Claude Projects and RAG systems.

Features:
- Analysis-first routing for optimal tool selection
- Support for native PDFs and scanned documents (OCR)
- Intelligent figure/chart to text conversion
- Quality scoring with automatic fallback
- RAG-optimized output with YAML frontmatter

Usage:
    from pdf_to_md_converter import convert_pdf_to_markdown

    success, message, output_path = convert_pdf_to_markdown(
        pdf_path="/path/to/document.pdf",
        output_dir="/path/to/output",
        accuracy_critical=False
    )
"""

import os
import re
import sys
import json
import tempfile
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Any, Union
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

# Script version for tracking conversions
CONVERTER_VERSION = "2.0.0"

# ============================================================================
# DEPENDENCY CHECKS
# ============================================================================

# PyMuPDF (fitz) - Fast text extraction
PYMUPDF_AVAILABLE = False
fitz = None
try:
    import fitz
    PYMUPDF_AVAILABLE = True
except ImportError:
    pass

# pdfplumber - Excellent table extraction
PDFPLUMBER_AVAILABLE = False
pdfplumber = None
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    pass

# Marker - Best overall quality, layout-aware
MARKER_AVAILABLE = False
marker_convert = None
try:
    from marker.convert import convert_single_pdf
    from marker.models import load_all_models
    MARKER_AVAILABLE = True
    marker_convert = convert_single_pdf
except ImportError:
    pass

# pytesseract - OCR for scanned documents
TESSERACT_AVAILABLE = False
pytesseract = None
try:
    import pytesseract
    # Test if tesseract is actually installed
    pytesseract.get_tesseract_version()
    TESSERACT_AVAILABLE = True
except (ImportError, Exception):
    pass

# PIL for image processing
PIL_AVAILABLE = False
Image = None
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    pass

# Try to import utilities from html_to_md_converter
HTML_CONVERTER_AVAILABLE = False
clean_markdown_for_rag = None
calculate_reading_time = None
sanitize_filename = None
try:
    from html_to_md_converter import (
        clean_markdown_for_rag,
        calculate_reading_time,
        sanitize_filename,
        extract_toc_from_markdown,
        remove_marketing_content,
    )
    HTML_CONVERTER_AVAILABLE = True
except ImportError:
    pass


# ============================================================================
# ENUMS AND DATA CLASSES
# ============================================================================

class DocumentType(Enum):
    """Classification of PDF document types for routing."""
    TEXT_HEAVY = "text_heavy"       # Books, articles
    TABLE_HEAVY = "table_heavy"     # Financial reports, data sheets
    MIXED_LAYOUT = "mixed_layout"   # Whitepapers with text/tables/figures
    IMAGE_HEAVY = "image_heavy"     # Presentations, image-rich docs
    SCANNED = "scanned"             # Needs OCR first


class ExtractionTool(Enum):
    """Available extraction tools."""
    PYMUPDF = "pymupdf"
    PDFPLUMBER = "pdfplumber"
    MARKER = "marker"
    OCR_THEN_MARKER = "ocr_then_marker"
    OCR_THEN_PYMUPDF = "ocr_then_pymupdf"


@dataclass
class FigureInfo:
    """Information about a detected figure/chart in the PDF."""
    page: int
    bbox: List[float]  # [x0, y0, x1, y1]
    fig_type: str      # "chart", "diagram", "image", "table_image"
    has_text: bool
    confidence: float = 0.0


@dataclass
class PDFAnalysis:
    """Analysis results for a PDF document."""
    has_text_layer: bool
    page_count: int
    text_density: float  # chars per page average
    table_count: int
    figure_count: int
    is_multi_column: bool
    font_count: int
    document_type: DocumentType
    recommended_tool: ExtractionTool
    figures: List[FigureInfo] = field(default_factory=list)
    total_chars: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversionScore:
    """Quality score for a conversion attempt."""
    overall_score: float
    completeness: float      # text captured vs expected
    structure: float         # headers, hierarchy
    table_integrity: float   # rows/columns preserved
    readability: float       # no garbage characters
    issues: List[str] = field(default_factory=list)


@dataclass
class ExtractedFigure:
    """A figure extracted and converted to text."""
    ref: str
    page: int
    fig_type: str
    confidence: float
    title: str
    extracted_data: str      # Markdown table if applicable
    description: str


# ============================================================================
# UTILITY FUNCTIONS (fallbacks if html_to_md_converter not available)
# ============================================================================

def _sanitize_filename_fallback(text: str) -> str:
    """Fallback sanitize filename if html_to_md_converter not available."""
    if not text:
        return ""
    text = re.sub(r'[<>:"/\\|?*]', '', text)
    text = re.sub(r'[:;]', ' -', text)
    text = re.sub(r'[\s_]+', ' ', text)
    text = text.strip()
    if len(text) > 80:
        text = text[:80].rsplit(' ', 1)[0]
    return text


def _calculate_reading_time_fallback(text: str) -> int:
    """Fallback reading time calculation."""
    words = re.findall(r'\b[a-zA-Z]{2,}\b', text)
    return max(1, round(len(words) / 225))


def _clean_markdown_fallback(content: str) -> str:
    """Fallback markdown cleaning."""
    # Remove excessive blank lines
    content = re.sub(r'\n{3,}', '\n\n', content)
    # Remove trailing whitespace
    content = '\n'.join(line.rstrip() for line in content.split('\n'))
    # Ensure file ends with newline
    return content.strip() + '\n'


# Use imported functions or fallbacks
if not HTML_CONVERTER_AVAILABLE:
    sanitize_filename = _sanitize_filename_fallback
    calculate_reading_time = _calculate_reading_time_fallback
    clean_markdown_for_rag = _clean_markdown_fallback

    def extract_toc_from_markdown(content: str) -> List[Dict[str, Any]]:
        """Fallback TOC extraction."""
        toc = []
        heading_pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
        for match in heading_pattern.finditer(content):
            level = len(match.group(1))
            text = match.group(2).strip()
            if text and len(text) > 2:
                toc.append({'text': text, 'level': level})
        return toc

    def remove_marketing_content(content: str) -> str:
        """Fallback - just return content as-is."""
        return content


# ============================================================================
# PDF ANALYZER
# ============================================================================

def analyze_pdf(pdf_path: str) -> PDFAnalysis:
    """
    Analyze a PDF to determine optimal conversion strategy.

    Returns:
        PDFAnalysis with document characteristics and recommended tool
    """
    if not PYMUPDF_AVAILABLE:
        raise RuntimeError("PyMuPDF (fitz) is required for PDF analysis. Install with: pip install pymupdf")

    print(f"      Analyzing PDF structure...")

    doc = fitz.open(pdf_path)
    page_count = len(doc)

    total_chars = 0
    total_tables = 0
    total_figures = 0
    fonts = set()
    multi_column_pages = 0
    figures_info = []

    # Sample pages for analysis (first, middle, last, and some random)
    sample_pages = list(set([
        0,
        page_count // 4,
        page_count // 2,
        3 * page_count // 4,
        page_count - 1
    ]))
    sample_pages = [p for p in sample_pages if p < page_count]

    for page_num in range(page_count):
        page = doc[page_num]

        # Get text
        text = page.get_text()
        total_chars += len(text)

        # Count fonts on sample pages
        if page_num in sample_pages:
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                if block.get("type") == 0:  # Text block
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            fonts.add(span.get("font", ""))

        # Detect potential tables (heuristic: many horizontal/vertical lines)
        drawings = page.get_drawings()
        h_lines = 0
        v_lines = 0
        for drawing in drawings:
            for item in drawing.get("items", []):
                if item[0] == "l":  # Line
                    x0, y0, x1, y1 = item[1:5]
                    if abs(y0 - y1) < 2:  # Horizontal
                        h_lines += 1
                    elif abs(x0 - x1) < 2:  # Vertical
                        v_lines += 1

        if h_lines > 5 and v_lines > 5:
            total_tables += 1

        # Detect figures/images
        images = page.get_images()
        for img_index, img in enumerate(images):
            xref = img[0]
            # Get image rect
            for img_rect in page.get_image_rects(xref):
                # Large images are likely figures
                width = img_rect.width
                height = img_rect.height
                if width > 100 and height > 100:
                    total_figures += 1
                    figures_info.append(FigureInfo(
                        page=page_num,
                        bbox=[img_rect.x0, img_rect.y0, img_rect.x1, img_rect.y1],
                        fig_type="image",
                        has_text=False,
                        confidence=0.7
                    ))

        # Detect multi-column layout (text blocks at similar y with different x)
        if page_num in sample_pages:
            text_dict = page.get_text("dict")
            blocks = text_dict.get("blocks", [])
            text_blocks = [b for b in blocks if b.get("type") == 0]
            if len(text_blocks) > 2:
                # Check if blocks have overlapping y ranges but different x
                for i, b1 in enumerate(text_blocks):
                    for b2 in text_blocks[i+1:]:
                        y_overlap = (b1["bbox"][1] < b2["bbox"][3] and b2["bbox"][1] < b1["bbox"][3])
                        x_separate = (b1["bbox"][2] < b2["bbox"][0] - 50) or (b2["bbox"][2] < b1["bbox"][0] - 50)
                        if y_overlap and x_separate:
                            multi_column_pages += 1
                            break

    # Extract metadata
    metadata = doc.metadata or {}
    doc.close()

    # Calculate metrics
    text_density = total_chars / page_count if page_count > 0 else 0
    is_multi_column = multi_column_pages > len(sample_pages) * 0.3

    # Determine if scanned (very low text density with images)
    has_text_layer = text_density > 100  # At least 100 chars per page
    is_scanned = not has_text_layer and total_figures > 0

    # Classify document type
    if is_scanned:
        doc_type = DocumentType.SCANNED
        recommended_tool = ExtractionTool.OCR_THEN_MARKER if MARKER_AVAILABLE else ExtractionTool.OCR_THEN_PYMUPDF
    elif total_figures > page_count * 0.5:
        # More than 50% of pages have figures
        doc_type = DocumentType.IMAGE_HEAVY
        recommended_tool = ExtractionTool.MARKER if MARKER_AVAILABLE else ExtractionTool.PYMUPDF
    elif total_tables > page_count * 0.3:
        # More than 30% of pages have tables
        doc_type = DocumentType.TABLE_HEAVY
        recommended_tool = ExtractionTool.PDFPLUMBER if PDFPLUMBER_AVAILABLE else ExtractionTool.MARKER
    elif total_tables > 0 or total_figures > 0:
        doc_type = DocumentType.MIXED_LAYOUT
        recommended_tool = ExtractionTool.MARKER if MARKER_AVAILABLE else ExtractionTool.PDFPLUMBER
    else:
        doc_type = DocumentType.TEXT_HEAVY
        recommended_tool = ExtractionTool.PYMUPDF if PYMUPDF_AVAILABLE else ExtractionTool.MARKER

    # Override if tool not available
    if recommended_tool == ExtractionTool.MARKER and not MARKER_AVAILABLE:
        recommended_tool = ExtractionTool.PDFPLUMBER if PDFPLUMBER_AVAILABLE else ExtractionTool.PYMUPDF
    if recommended_tool == ExtractionTool.PDFPLUMBER and not PDFPLUMBER_AVAILABLE:
        recommended_tool = ExtractionTool.PYMUPDF

    print(f"      Pages: {page_count}, Chars: {total_chars:,}, Tables: {total_tables}, Figures: {total_figures}")
    print(f"      Document type: {doc_type.value}, Recommended tool: {recommended_tool.value}")

    return PDFAnalysis(
        has_text_layer=has_text_layer,
        page_count=page_count,
        text_density=text_density,
        table_count=total_tables,
        figure_count=total_figures,
        is_multi_column=is_multi_column,
        font_count=len(fonts),
        document_type=doc_type,
        recommended_tool=recommended_tool,
        figures=figures_info,
        total_chars=total_chars,
        metadata=metadata
    )


# ============================================================================
# CONVERSION TOOL WRAPPERS
# ============================================================================

def convert_with_pymupdf(pdf_path: str, analysis: PDFAnalysis) -> Tuple[str, Dict[str, Any]]:
    """
    Convert PDF using PyMuPDF (fast text extraction).

    Best for: TEXT_HEAVY documents

    Returns:
        Tuple of (markdown_content, metadata)
    """
    if not PYMUPDF_AVAILABLE:
        raise RuntimeError("PyMuPDF not available")

    print("      Using PyMuPDF for conversion...")

    doc = fitz.open(pdf_path)
    content_parts = []

    for page_num in range(len(doc)):
        page = doc[page_num]

        # Get text with formatting hints
        text = page.get_text("text")

        if text.strip():
            # Try to detect headers based on font size
            text_dict = page.get_text("dict")
            processed_text = _process_pymupdf_page(text_dict)
            content_parts.append(processed_text)

        # Add page break marker
        if page_num < len(doc) - 1:
            content_parts.append("\n---\n")

    # Extract metadata
    pdf_metadata = doc.metadata or {}
    doc.close()

    markdown_content = "\n\n".join(content_parts)

    metadata = {
        "title": pdf_metadata.get("title", ""),
        "author": pdf_metadata.get("author", ""),
        "subject": pdf_metadata.get("subject", ""),
        "keywords": pdf_metadata.get("keywords", ""),
        "creation_date": pdf_metadata.get("creationDate", ""),
        "extraction_tool": "pymupdf"
    }

    return markdown_content, metadata


def _process_pymupdf_page(text_dict: Dict) -> str:
    """Process PyMuPDF text dict to infer structure."""
    lines = []
    prev_size = None
    max_size = 0

    # First pass: find max font size
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:  # Text block
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = span.get("size", 12)
                    if size > max_size:
                        max_size = size

    # Second pass: process text with heading detection
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:  # Text block
            block_text_parts = []
            block_size = 0

            for line in block.get("lines", []):
                line_text_parts = []
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    size = span.get("size", 12)
                    flags = span.get("flags", 0)

                    if size > block_size:
                        block_size = size

                    # Bold detection
                    is_bold = bool(flags & 2 ** 4)

                    if is_bold and text.strip():
                        line_text_parts.append(f"**{text}**")
                    else:
                        line_text_parts.append(text)

                block_text_parts.append("".join(line_text_parts))

            block_text = "\n".join(block_text_parts).strip()

            if not block_text:
                continue

            # Determine if this is a heading based on size
            if max_size > 0 and block_size >= max_size * 0.9:
                # Likely a main heading (H1)
                lines.append(f"\n# {block_text}\n")
            elif max_size > 0 and block_size >= max_size * 0.75:
                # Likely a section heading (H2)
                lines.append(f"\n## {block_text}\n")
            elif max_size > 0 and block_size >= max_size * 0.6:
                # Likely a subsection heading (H3)
                lines.append(f"\n### {block_text}\n")
            else:
                lines.append(block_text)

    return "\n\n".join(lines)


def convert_with_pdfplumber(pdf_path: str, analysis: PDFAnalysis) -> Tuple[str, Dict[str, Any]]:
    """
    Convert PDF using pdfplumber (excellent table extraction).

    Best for: TABLE_HEAVY documents

    Returns:
        Tuple of (markdown_content, metadata)
    """
    if not PDFPLUMBER_AVAILABLE:
        raise RuntimeError("pdfplumber not available")

    print("      Using pdfplumber for conversion...")

    content_parts = []
    table_count = 0

    with pdfplumber.open(pdf_path) as pdf:
        metadata = pdf.metadata or {}

        for page_num, page in enumerate(pdf.pages):
            # Extract tables first
            tables = page.extract_tables()
            table_areas = []

            for table in tables:
                if table and len(table) > 1:
                    table_count += 1
                    md_table = _convert_table_to_markdown(table)
                    if md_table:
                        content_parts.append(md_table)

            # Extract text
            text = page.extract_text() or ""
            if text.strip():
                content_parts.append(text)

            # Page break
            if page_num < len(pdf.pages) - 1:
                content_parts.append("\n---\n")

    markdown_content = "\n\n".join(content_parts)

    result_metadata = {
        "title": metadata.get("Title", ""),
        "author": metadata.get("Author", ""),
        "subject": metadata.get("Subject", ""),
        "creation_date": metadata.get("CreationDate", ""),
        "extraction_tool": "pdfplumber",
        "tables_extracted": table_count
    }

    return markdown_content, result_metadata


def _convert_table_to_markdown(table: List[List]) -> str:
    """Convert a table to markdown format."""
    if not table or len(table) < 1:
        return ""

    # Clean up cells
    cleaned_table = []
    for row in table:
        cleaned_row = []
        for cell in row:
            if cell is None:
                cleaned_row.append("")
            else:
                # Clean and normalize cell content
                cell_text = str(cell).replace("\n", " ").replace("|", "\\|").strip()
                cleaned_row.append(cell_text)
        cleaned_table.append(cleaned_row)

    if not cleaned_table:
        return ""

    # Build markdown table
    lines = []

    # Header row
    header = cleaned_table[0]
    lines.append("| " + " | ".join(header) + " |")

    # Separator
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")

    # Data rows
    for row in cleaned_table[1:]:
        # Pad row to match header length
        while len(row) < len(header):
            row.append("")
        lines.append("| " + " | ".join(row[:len(header)]) + " |")

    return "\n".join(lines)


def convert_with_marker(pdf_path: str, analysis: PDFAnalysis) -> Tuple[str, Dict[str, Any]]:
    """
    Convert PDF using Marker (best overall quality, layout-aware).

    Best for: MIXED_LAYOUT and IMAGE_HEAVY documents

    Returns:
        Tuple of (markdown_content, metadata)
    """
    if not MARKER_AVAILABLE:
        raise RuntimeError("Marker not available. Install with: pip install marker-pdf")

    print("      Using Marker for conversion (this may take a while)...")

    # Load models (cached after first load)
    models = load_all_models()

    # Convert
    full_text, images, out_meta = marker_convert(
        pdf_path,
        models,
        batch_multiplier=2
    )

    # Marker returns markdown directly
    markdown_content = full_text

    metadata = {
        "title": out_meta.get("title", ""),
        "extraction_tool": "marker",
        "figures_extracted": len(images) if images else 0
    }

    return markdown_content, metadata


def convert_with_ocr(pdf_path: str, analysis: PDFAnalysis) -> Tuple[str, Dict[str, Any]]:
    """
    OCR the PDF first, then convert.

    Best for: SCANNED documents

    Returns:
        Tuple of (markdown_content, metadata)
    """
    if not TESSERACT_AVAILABLE:
        raise RuntimeError("Tesseract OCR not available. Install pytesseract and tesseract.")

    if not PYMUPDF_AVAILABLE:
        raise RuntimeError("PyMuPDF required for OCR preprocessing")

    if not PIL_AVAILABLE:
        raise RuntimeError("PIL/Pillow required for OCR preprocessing")

    print("      Running OCR on scanned document...")

    doc = fitz.open(pdf_path)
    content_parts = []

    for page_num in range(len(doc)):
        page = doc[page_num]

        # Render page to image
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better OCR
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        # Run OCR
        text = pytesseract.image_to_string(img, lang='eng')

        if text.strip():
            content_parts.append(text)

        # Page break
        if page_num < len(doc) - 1:
            content_parts.append("\n---\n")

        # Progress
        if (page_num + 1) % 10 == 0:
            print(f"      OCR progress: {page_num + 1}/{len(doc)} pages")

    pdf_metadata = doc.metadata or {}
    doc.close()

    markdown_content = "\n\n".join(content_parts)

    metadata = {
        "title": pdf_metadata.get("title", ""),
        "author": pdf_metadata.get("author", ""),
        "extraction_tool": "ocr_pytesseract",
        "ocr_applied": True
    }

    return markdown_content, metadata


# ============================================================================
# FIGURE EXTRACTOR
# ============================================================================

def extract_figure_as_text(pdf_path: str, figure_info: FigureInfo, page_content: str = "") -> ExtractedFigure:
    """
    Extract a figure and convert it to descriptive text.

    Since we don't include image files, figures become structured text blocks.

    Returns:
        ExtractedFigure with text description
    """
    # Determine figure reference
    ref = f"fig_{figure_info.page + 1}"

    # Try to extract any text caption near the figure
    title = "Untitled Figure"
    description = ""
    extracted_data = ""

    # Basic heuristic: look for "Figure X:" or "Fig. X" patterns near the figure
    fig_patterns = [
        r'(?:Figure|Fig\.?)\s*(\d+)[:\.]?\s*([^\n]+)',
        r'(?:Chart|Graph|Diagram)\s*(\d+)[:\.]?\s*([^\n]+)',
    ]

    for pattern in fig_patterns:
        match = re.search(pattern, page_content, re.IGNORECASE)
        if match:
            ref = f"fig_{match.group(1)}"
            title = match.group(2).strip() if match.group(2) else title
            break

    # Determine confidence based on figure type
    confidence = figure_info.confidence
    if confidence >= 0.8:
        description = f"{figure_info.fig_type.title()} on page {figure_info.page + 1}."
    elif confidence >= 0.5:
        description = f"{figure_info.fig_type.title()} on page {figure_info.page + 1}. Some details may be ambiguous."
    else:
        description = f"{figure_info.fig_type.title()} on page {figure_info.page + 1}. Low confidence extraction - verify against original PDF for critical details."

    return ExtractedFigure(
        ref=ref,
        page=figure_info.page + 1,
        fig_type=figure_info.fig_type,
        confidence=confidence,
        title=title,
        extracted_data=extracted_data,
        description=description
    )


def format_figure_as_markdown(figure: ExtractedFigure) -> str:
    """Format an extracted figure as a markdown block."""
    confidence_pct = int(figure.confidence * 100)

    lines = [
        f'<figure ref="{figure.ref}" page="{figure.page}" type="{figure.fig_type}" confidence="{confidence_pct}">',
        f'**{figure.title}**',
        ''
    ]

    if figure.extracted_data:
        lines.append(figure.extracted_data)
        lines.append('')

    lines.append(figure.description)

    if figure.confidence < 0.5:
        lines.append('')
        lines.append('*Low confidence extraction - verify against original PDF for critical details.*')

    lines.append('</figure>')

    return '\n'.join(lines)


# ============================================================================
# QUALITY SCORER
# ============================================================================

def score_conversion(original_analysis: PDFAnalysis, markdown_output: str) -> ConversionScore:
    """
    Score the quality of a conversion output.

    Returns:
        ConversionScore with overall and component scores
    """
    issues = []

    # Completeness: compare extracted chars to original
    extracted_chars = len(re.sub(r'\s+', '', markdown_output))
    expected_chars = original_analysis.total_chars * 0.8  # Allow 20% overhead removal

    if expected_chars > 0:
        completeness = min(1.0, extracted_chars / expected_chars)
    else:
        completeness = 0.5  # Unknown baseline

    if completeness < 0.5:
        issues.append("Significant text may be missing from output")

    # Structure: check for headers
    headers = re.findall(r'^#{1,6}\s+.+$', markdown_output, re.MULTILINE)
    expected_headers = max(3, original_analysis.page_count // 5)  # Rough estimate

    structure = min(1.0, len(headers) / expected_headers) if expected_headers > 0 else 0.5

    if len(headers) < 2:
        issues.append("Few or no headers detected - document structure may be lost")

    # Table integrity
    if original_analysis.table_count > 0:
        # Count markdown tables
        table_markers = len(re.findall(r'^\|.+\|$', markdown_output, re.MULTILINE))
        # Rough estimate: each table has at least 3 rows
        extracted_tables = table_markers // 3
        table_integrity = min(1.0, extracted_tables / original_analysis.table_count)

        if extracted_tables < original_analysis.table_count:
            issues.append(f"Some tables may not have been extracted ({extracted_tables}/{original_analysis.table_count})")
    else:
        table_integrity = 1.0

    # Readability: check for garbage characters
    garbage_chars = len(re.findall(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', markdown_output))
    total_chars = len(markdown_output)

    if total_chars > 0:
        garbage_ratio = garbage_chars / total_chars
        readability = max(0, 1.0 - garbage_ratio * 10)  # 10% garbage = 0 score
    else:
        readability = 0.0

    if garbage_ratio > 0.01:
        issues.append("Output contains garbage/non-printable characters")

    # Check for repeated characters (OCR artifacts)
    repeated = len(re.findall(r'(.)\1{5,}', markdown_output))
    if repeated > 10:
        readability *= 0.8
        issues.append("Possible OCR artifacts detected (repeated characters)")

    # Overall score (weighted average)
    overall_score = (
        completeness * 0.35 +
        structure * 0.20 +
        table_integrity * 0.25 +
        readability * 0.20
    )

    return ConversionScore(
        overall_score=overall_score,
        completeness=completeness,
        structure=structure,
        table_integrity=table_integrity,
        readability=readability,
        issues=issues
    )


# ============================================================================
# YAML FRONTMATTER
# ============================================================================

def generate_pdf_yaml_frontmatter(
    metadata: Dict[str, Any],
    pdf_path: str,
    analysis: PDFAnalysis,
    reading_time: int,
    score: ConversionScore,
    extraction_tool: str,
    ocr_applied: bool = False
) -> str:
    """Generate YAML frontmatter for PDF markdown output."""
    lines = ['---']

    # Title
    title = metadata.get('title', '') or Path(pdf_path).stem
    title = title.replace('"', '\\"')
    lines.append(f'title: "{title}"')

    # Author
    author = metadata.get('author', 'Unknown')
    author = author.replace('"', '\\"')
    lines.append(f'author: "{author}"')

    # Source info
    lines.append('source_type: "pdf"')
    lines.append(f'source_file: "{Path(pdf_path).name}"')

    # Dates
    creation_date = metadata.get('creation_date', '')
    if creation_date:
        # Try to parse PDF date format: D:YYYYMMDDHHmmSS
        match = re.match(r"D:(\d{4})(\d{2})(\d{2})", creation_date)
        if match:
            creation_date = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        lines.append(f'publication_date: "{creation_date}"')

    lines.append(f'retrieved_date: "{datetime.now().strftime("%Y-%m-%d")}"')

    # Document stats
    lines.append(f'reading_time_minutes: {reading_time}')
    lines.append(f'page_count: {analysis.page_count}')
    lines.append(f'figures_extracted: {analysis.figure_count}')
    lines.append(f'tables_extracted: {analysis.table_count}')

    # Quality info
    lines.append(f'extraction_confidence: {score.overall_score:.2f}')
    lines.append(f'extraction_tool: "{extraction_tool}"')
    lines.append(f'ocr_applied: {str(ocr_applied).lower()}')
    lines.append(f'content_type: "{analysis.document_type.value}"')

    # Version
    lines.append(f'converter_version: "{CONVERTER_VERSION}"')

    lines.append('---')

    return '\n'.join(lines)


# ============================================================================
# MAIN CONVERTER
# ============================================================================

def check_dependencies() -> Tuple[bool, List[str]]:
    """Check if required dependencies are installed."""
    missing = []

    if not PYMUPDF_AVAILABLE:
        missing.append('pymupdf')

    # At least one conversion tool must be available
    if not (PYMUPDF_AVAILABLE or PDFPLUMBER_AVAILABLE or MARKER_AVAILABLE):
        missing.append('(pymupdf or pdfplumber or marker-pdf)')

    return len(missing) == 0, missing


def convert_pdf_to_markdown(
    pdf_path: str,
    output_dir: str,
    accuracy_critical: bool = False
) -> Tuple[bool, str, Optional[str]]:
    """
    Main entry point for PDF to Markdown conversion.

    Args:
        pdf_path: Path to the PDF file
        output_dir: Directory to save the output Markdown file
        accuracy_critical: If True, use higher quality threshold (0.93 vs 0.85)

    Returns:
        Tuple of (success, message, output_filepath)
    """
    # Check dependencies
    deps_ok, missing = check_dependencies()
    if not deps_ok:
        return False, f"Missing required dependencies: {', '.join(missing)}. Install with pip.", None

    # Validate input
    pdf_path = str(Path(pdf_path).resolve())
    if not os.path.exists(pdf_path):
        return False, f"PDF file not found: {pdf_path}", None

    if not pdf_path.lower().endswith('.pdf'):
        return False, f"File is not a PDF: {pdf_path}", None

    print(f"\n{'='*60}")
    print(f"Converting PDF: {Path(pdf_path).name}")
    print('='*60)

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    try:
        # Step 1: Analyze PDF
        print("\n[1/6] Analyzing PDF structure...")
        analysis = analyze_pdf(pdf_path)

        # Step 2: Select and run conversion tool
        print(f"\n[2/6] Converting with {analysis.recommended_tool.value}...")

        quality_threshold = 0.93 if accuracy_critical else 0.85
        tools_tried = []
        best_result = None
        best_score = None

        # Define tool order based on document type
        tool_order = _get_tool_order(analysis)

        for tool in tool_order:
            if tool in tools_tried:
                continue

            tools_tried.append(tool)

            try:
                if tool == ExtractionTool.PYMUPDF and PYMUPDF_AVAILABLE:
                    markdown_content, metadata = convert_with_pymupdf(pdf_path, analysis)
                elif tool == ExtractionTool.PDFPLUMBER and PDFPLUMBER_AVAILABLE:
                    markdown_content, metadata = convert_with_pdfplumber(pdf_path, analysis)
                elif tool == ExtractionTool.MARKER and MARKER_AVAILABLE:
                    markdown_content, metadata = convert_with_marker(pdf_path, analysis)
                elif tool in (ExtractionTool.OCR_THEN_MARKER, ExtractionTool.OCR_THEN_PYMUPDF) and TESSERACT_AVAILABLE:
                    markdown_content, metadata = convert_with_ocr(pdf_path, analysis)
                else:
                    continue

                # Score the result
                score = score_conversion(analysis, markdown_content)
                print(f"      Conversion score: {score.overall_score:.2f}")

                if score.issues:
                    for issue in score.issues:
                        print(f"      Warning: {issue}")

                if best_result is None or score.overall_score > best_score.overall_score:
                    best_result = (markdown_content, metadata)
                    best_score = score

                # Check if quality is acceptable
                if score.overall_score >= quality_threshold:
                    print(f"      Score meets threshold ({quality_threshold:.2f})")
                    break
                else:
                    print(f"      Score below threshold ({quality_threshold:.2f}), trying next tool...")

            except Exception as e:
                print(f"      {tool.value} failed: {e}")
                continue

        if best_result is None:
            return False, "All conversion tools failed", None

        markdown_content, metadata = best_result

        # Step 3: Extract figures as text
        print("\n[3/6] Processing figures...")
        if analysis.figures:
            figure_blocks = []
            for fig_info in analysis.figures[:20]:  # Limit to first 20 figures
                figure = extract_figure_as_text(pdf_path, fig_info, markdown_content)
                figure_blocks.append(format_figure_as_markdown(figure))

            if figure_blocks:
                markdown_content += "\n\n## Figures\n\n" + "\n\n".join(figure_blocks)

            print(f"      Processed {len(figure_blocks)} figures")
        else:
            print("      No figures to process")

        # Step 4: Clean content for RAG
        print("\n[4/6] Cleaning content for RAG...")
        markdown_content = clean_markdown_for_rag(markdown_content)
        print(f"      Cleaned content: {len(markdown_content):,} characters")

        # Step 5: Calculate reading time and TOC
        print("\n[5/6] Generating metadata...")
        reading_time = calculate_reading_time(markdown_content)
        toc = extract_toc_from_markdown(markdown_content)
        print(f"      Reading time: {reading_time} minutes")
        print(f"      TOC sections: {len(toc)}")

        # Step 6: Generate output
        print("\n[6/6] Generating output file...")

        # Determine OCR status
        ocr_applied = analysis.document_type == DocumentType.SCANNED

        # Generate frontmatter
        frontmatter = generate_pdf_yaml_frontmatter(
            metadata=metadata,
            pdf_path=pdf_path,
            analysis=analysis,
            reading_time=reading_time,
            score=best_score,
            extraction_tool=tools_tried[-1].value if tools_tried else "unknown",
            ocr_applied=ocr_applied
        )

        # Build TOC if available
        toc_markdown = ""
        if len(toc) >= 3:
            toc_markdown = "\n## Table of Contents\n\n"
            min_level = min(item['level'] for item in toc)
            for item in toc:
                indent = "  " * (item['level'] - min_level)
                toc_markdown += f"{indent}- {item['text']}\n"
            toc_markdown += "\n"

        # Combine all parts
        final_content = frontmatter + "\n\n" + toc_markdown + markdown_content

        # Generate filename
        title = metadata.get('title', '') or Path(pdf_path).stem
        author = metadata.get('author', 'Unknown')
        filename = f"{sanitize_filename(author)} - {sanitize_filename(title)}.md"

        # Write file
        filepath = output_path / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(final_content)

        file_size = filepath.stat().st_size / 1024

        print(f"\n{'='*60}")
        print(f"SUCCESS!")
        print(f"Output: {filepath}")
        print(f"Size: {file_size:.1f} KB")
        print(f"Quality Score: {best_score.overall_score:.2f}")
        print('='*60)

        return True, f"Successfully converted to: {filename}", str(filepath)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"Conversion failed: {str(e)}", None


def _get_tool_order(analysis: PDFAnalysis) -> List[ExtractionTool]:
    """Get ordered list of tools to try based on document type."""
    if analysis.document_type == DocumentType.SCANNED:
        return [
            ExtractionTool.OCR_THEN_MARKER,
            ExtractionTool.OCR_THEN_PYMUPDF,
        ]
    elif analysis.document_type == DocumentType.TEXT_HEAVY:
        return [
            ExtractionTool.PYMUPDF,
            ExtractionTool.MARKER,
            ExtractionTool.PDFPLUMBER,
        ]
    elif analysis.document_type == DocumentType.TABLE_HEAVY:
        return [
            ExtractionTool.PDFPLUMBER,
            ExtractionTool.MARKER,
            ExtractionTool.PYMUPDF,
        ]
    elif analysis.document_type == DocumentType.IMAGE_HEAVY:
        return [
            ExtractionTool.MARKER,
            ExtractionTool.PYMUPDF,
            ExtractionTool.PDFPLUMBER,
        ]
    else:  # MIXED_LAYOUT
        return [
            ExtractionTool.MARKER,
            ExtractionTool.PDFPLUMBER,
            ExtractionTool.PYMUPDF,
        ]


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

def main():
    """Main entry point for CLI usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Convert PDF documents to AI-optimized Markdown',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pdf_to_md_converter.py document.pdf
  python pdf_to_md_converter.py document.pdf -o ./output
  python pdf_to_md_converter.py financial_report.pdf --accuracy-critical
        """
    )

    parser.add_argument('pdf_path', help='Path to the PDF file to convert')
    parser.add_argument('-o', '--output', default='./converted_pdfs',
                        help='Output directory (default: ./converted_pdfs)')
    parser.add_argument('--accuracy-critical', action='store_true',
                        help='Use higher quality threshold for financial/scientific documents')
    parser.add_argument('--check-deps', action='store_true',
                        help='Check available dependencies and exit')

    args = parser.parse_args()

    if args.check_deps:
        print("PDF to Markdown Converter - Dependency Check")
        print("=" * 50)
        print(f"PyMuPDF (fitz):    {'OK' if PYMUPDF_AVAILABLE else 'MISSING - pip install pymupdf'}")
        print(f"pdfplumber:        {'OK' if PDFPLUMBER_AVAILABLE else 'MISSING - pip install pdfplumber'}")
        print(f"Marker:            {'OK' if MARKER_AVAILABLE else 'MISSING - pip install marker-pdf'}")
        print(f"Tesseract OCR:     {'OK' if TESSERACT_AVAILABLE else 'MISSING - pip install pytesseract + install tesseract'}")
        print(f"PIL/Pillow:        {'OK' if PIL_AVAILABLE else 'MISSING - pip install Pillow'}")
        print(f"HTML Converter:    {'OK' if HTML_CONVERTER_AVAILABLE else 'MISSING (optional)'}")
        sys.exit(0)

    success, message, filepath = convert_pdf_to_markdown(
        pdf_path=args.pdf_path,
        output_dir=args.output,
        accuracy_critical=args.accuracy_critical
    )

    if success:
        print(f"\n{message}")
        sys.exit(0)
    else:
        print(f"\nError: {message}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
