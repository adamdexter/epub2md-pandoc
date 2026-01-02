#!/usr/bin/env python3
"""
HTML to Markdown Web Article Converter
Converts web articles (blog posts, Medium articles, etc.) to AI-optimized Markdown.
Designed for Claude Projects and RAG systems.
"""

import os
import re
import sys
import json
import hashlib
import mimetypes
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Any
from datetime import datetime
from urllib.parse import urlparse, urljoin
import html

# Script version for tracking conversions
CONVERTER_VERSION = "1.0.0"

# Try to import required libraries
TRAFILATURA_AVAILABLE = False
READABILITY_AVAILABLE = False
REQUESTS_AVAILABLE = False
BS4_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    pass

try:
    import trafilatura
    from trafilatura import extract
    from trafilatura.metadata import extract_metadata
    TRAFILATURA_AVAILABLE = True
except ImportError:
    pass

try:
    from readability import Document
    READABILITY_AVAILABLE = True
except ImportError:
    pass

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    pass


# Default headers to mimic a real browser
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}


def check_dependencies() -> Tuple[bool, List[str]]:
    """Check if required dependencies are installed."""
    missing = []

    if not REQUESTS_AVAILABLE:
        missing.append('requests')
    if not TRAFILATURA_AVAILABLE:
        missing.append('trafilatura')
    if not BS4_AVAILABLE:
        missing.append('beautifulsoup4')

    # readability-lxml is optional (fallback)

    return len(missing) == 0, missing


def fetch_url(url: str, timeout: int = 30) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch URL content with proper headers.

    Returns:
        Tuple of (html_content, error_message)
    """
    if not REQUESTS_AVAILABLE:
        return None, "requests library not installed"

    try:
        response = requests.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            allow_redirects=True
        )
        response.raise_for_status()

        # Try to detect encoding
        response.encoding = response.apparent_encoding or 'utf-8'

        return response.text, None

    except requests.exceptions.Timeout:
        return None, f"Request timed out after {timeout} seconds"
    except requests.exceptions.HTTPError as e:
        return None, f"HTTP error: {e.response.status_code} - {e.response.reason}"
    except requests.exceptions.ConnectionError:
        return None, "Connection error - check your internet connection"
    except requests.exceptions.RequestException as e:
        return None, f"Request failed: {str(e)}"


def extract_json_ld_metadata(html_content: str) -> Dict[str, Any]:
    """Extract metadata from JSON-LD structured data."""
    metadata = {}

    if not BS4_AVAILABLE:
        return metadata

    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        # Find all JSON-LD scripts
        json_ld_scripts = soup.find_all('script', type='application/ld+json')

        for script in json_ld_scripts:
            try:
                data = json.loads(script.string)

                # Handle @graph structure
                if '@graph' in data:
                    for item in data['@graph']:
                        metadata.update(parse_json_ld_item(item))
                else:
                    metadata.update(parse_json_ld_item(data))

            except (json.JSONDecodeError, TypeError):
                continue

    except Exception:
        pass

    return metadata


def parse_json_ld_item(item: Dict) -> Dict[str, Any]:
    """Parse a single JSON-LD item for metadata."""
    metadata = {}

    item_type = item.get('@type', '')

    # Handle Article types
    if item_type in ['Article', 'BlogPosting', 'NewsArticle', 'WebPage', 'TechArticle']:
        if 'headline' in item:
            metadata['title'] = item['headline']
        elif 'name' in item:
            metadata['title'] = item['name']

        # Author extraction
        author = item.get('author')
        if author:
            if isinstance(author, dict):
                metadata['author'] = author.get('name', '')
            elif isinstance(author, list) and author:
                names = [a.get('name', '') if isinstance(a, dict) else str(a) for a in author]
                metadata['author'] = ', '.join(filter(None, names))
            elif isinstance(author, str):
                metadata['author'] = author

        # Date extraction
        if 'datePublished' in item:
            metadata['publication_date'] = item['datePublished']
        elif 'dateCreated' in item:
            metadata['publication_date'] = item['dateCreated']

        if 'description' in item:
            metadata['description'] = item['description']

        if 'publisher' in item:
            publisher = item['publisher']
            if isinstance(publisher, dict):
                metadata['publisher'] = publisher.get('name', '')
            elif isinstance(publisher, str):
                metadata['publisher'] = publisher

        if 'image' in item:
            img = item['image']
            if isinstance(img, str):
                metadata['main_image'] = img
            elif isinstance(img, dict):
                metadata['main_image'] = img.get('url', '')
            elif isinstance(img, list) and img:
                first_img = img[0]
                if isinstance(first_img, str):
                    metadata['main_image'] = first_img
                elif isinstance(first_img, dict):
                    metadata['main_image'] = first_img.get('url', '')

    return metadata


def extract_opengraph_metadata(html_content: str) -> Dict[str, Any]:
    """Extract metadata from OpenGraph and Twitter meta tags."""
    metadata = {}

    if not BS4_AVAILABLE:
        return metadata

    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        # OpenGraph tags
        og_mappings = {
            'og:title': 'title',
            'og:description': 'description',
            'og:image': 'main_image',
            'og:site_name': 'source_name',
            'og:url': 'canonical_url',
            'article:author': 'author',
            'article:published_time': 'publication_date',
            'article:modified_time': 'modified_date',
        }

        for og_property, meta_key in og_mappings.items():
            meta = soup.find('meta', property=og_property)
            if meta and meta.get('content'):
                metadata[meta_key] = meta['content']

        # Twitter card tags (fallback)
        twitter_mappings = {
            'twitter:title': 'title',
            'twitter:description': 'description',
            'twitter:image': 'main_image',
            'twitter:creator': 'twitter_author',
        }

        for twitter_name, meta_key in twitter_mappings.items():
            if meta_key not in metadata:
                meta = soup.find('meta', attrs={'name': twitter_name})
                if meta and meta.get('content'):
                    metadata[meta_key] = meta['content']

        # Standard meta tags
        standard_mappings = {
            'author': 'author',
            'description': 'description',
            'date': 'publication_date',
            'publish_date': 'publication_date',
        }

        for meta_name, meta_key in standard_mappings.items():
            if meta_key not in metadata:
                meta = soup.find('meta', attrs={'name': meta_name})
                if meta and meta.get('content'):
                    metadata[meta_key] = meta['content']

    except Exception:
        pass

    return metadata


def extract_tags_and_topics(html_content: str) -> List[str]:
    """Extract tags, topics, categories from the article."""
    tags = set()

    if not BS4_AVAILABLE:
        return []

    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        # JSON-LD keywords
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict):
                    keywords = data.get('keywords', [])
                    if isinstance(keywords, str):
                        tags.update(k.strip() for k in keywords.split(','))
                    elif isinstance(keywords, list):
                        tags.update(str(k).strip() for k in keywords)
            except (json.JSONDecodeError, TypeError):
                continue

        # Meta keywords
        meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
        if meta_keywords and meta_keywords.get('content'):
            tags.update(k.strip() for k in meta_keywords['content'].split(','))

        # Article tags (common patterns)
        tag_selectors = [
            ('a', {'rel': 'tag'}),
            ('a', {'class': re.compile(r'tag|topic|category', re.I)}),
            ('span', {'class': re.compile(r'tag|topic|category', re.I)}),
            ('li', {'class': re.compile(r'tag|topic|category', re.I)}),
        ]

        for tag_name, attrs in tag_selectors:
            for elem in soup.find_all(tag_name, attrs):
                text = elem.get_text(strip=True)
                if text and len(text) < 50:  # Sanity check
                    tags.add(text)

        # OpenGraph article tags
        for meta in soup.find_all('meta', property='article:tag'):
            if meta.get('content'):
                tags.add(meta['content'].strip())

        # Clean up tags
        cleaned_tags = []
        for tag in tags:
            tag = tag.strip()
            if tag and len(tag) > 1 and not tag.startswith('#'):
                cleaned_tags.append(tag)

    except Exception:
        pass

    return sorted(set(cleaned_tags))[:20]  # Limit to 20 tags


def extract_table_of_contents(html_content: str) -> List[Dict[str, Any]]:
    """Extract table of contents from headings in the article."""
    toc = []

    if not BS4_AVAILABLE:
        return toc

    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        # First, try to find an explicit TOC
        toc_selectors = [
            ('nav', {'class': re.compile(r'toc|table-of-contents', re.I)}),
            ('div', {'class': re.compile(r'toc|table-of-contents', re.I)}),
            ('ul', {'class': re.compile(r'toc|table-of-contents', re.I)}),
        ]

        for tag_name, attrs in toc_selectors:
            toc_elem = soup.find(tag_name, attrs)
            if toc_elem:
                for link in toc_elem.find_all('a'):
                    text = link.get_text(strip=True)
                    if text:
                        toc.append({'text': text, 'level': 2})
                if toc:
                    return toc

        # If no explicit TOC, build from headings
        # Find article content first
        article = soup.find('article') or soup.find('main') or soup.find('div', class_=re.compile(r'content|post|article', re.I))
        search_area = article if article else soup

        for heading in search_area.find_all(['h1', 'h2', 'h3', 'h4']):
            text = heading.get_text(strip=True)
            if text and len(text) < 200:  # Skip very long headings
                level = int(heading.name[1])
                toc.append({'text': text, 'level': level})

    except Exception:
        pass

    return toc


def format_toc_for_markdown(toc: List[Dict[str, Any]], title: str = None) -> str:
    """Format table of contents as markdown."""
    if not toc:
        return ""

    lines = ["## Table of Contents\n"]

    # Find minimum level to normalize indentation
    min_level = min(item['level'] for item in toc) if toc else 2

    for item in toc:
        indent = "  " * (item['level'] - min_level)
        lines.append(f"{indent}- {item['text']}")

    return '\n'.join(lines) + '\n'


def extract_html_metadata(html_content: str, url: str) -> Dict[str, Any]:
    """Extract metadata by parsing HTML structure."""
    metadata = {}

    if not BS4_AVAILABLE:
        return metadata

    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        # Title from <title> tag
        title_tag = soup.find('title')
        if title_tag and title_tag.string:
            # Clean up title (often includes site name)
            title = title_tag.string.strip()
            # Remove common separators with site name
            for sep in [' | ', ' - ', ' — ', ' :: ', ' // ']:
                if sep in title:
                    parts = title.split(sep)
                    # Usually the article title is the first or longest part
                    title = max(parts, key=len).strip()
                    break
            metadata['title'] = title

        # Try to find author from common patterns
        author_selectors = [
            ('meta', {'name': 'author'}),
            ('a', {'rel': 'author'}),
            ('span', {'class': re.compile(r'author|byline', re.I)}),
            ('div', {'class': re.compile(r'author|byline', re.I)}),
            ('p', {'class': re.compile(r'author|byline', re.I)}),
        ]

        for tag, attrs in author_selectors:
            if 'author' not in metadata:
                elem = soup.find(tag, attrs)
                if elem:
                    if tag == 'meta':
                        metadata['author'] = elem.get('content', '')
                    else:
                        # Get text content
                        text = elem.get_text(strip=True)
                        # Clean up common prefixes
                        text = re.sub(r'^(by|written by|author:?)\s*', '', text, flags=re.I)
                        if text and len(text) < 100:  # Sanity check
                            metadata['author'] = text

        # Try to find date from time elements or common patterns
        time_elem = soup.find('time')
        if time_elem:
            datetime_attr = time_elem.get('datetime')
            if datetime_attr:
                metadata['publication_date'] = datetime_attr
            elif time_elem.string:
                metadata['publication_date'] = time_elem.string.strip()

        # Extract site name from URL if not found
        if 'source_name' not in metadata:
            parsed = urlparse(url)
            domain = parsed.netloc.replace('www.', '')
            # Capitalize domain parts
            parts = domain.split('.')
            if parts:
                metadata['source_name'] = parts[0].capitalize()

    except Exception:
        pass

    return metadata


def merge_metadata(*metadata_dicts: Dict[str, Any]) -> Dict[str, Any]:
    """Merge multiple metadata dictionaries, preferring earlier sources."""
    merged = {}

    for md in metadata_dicts:
        for key, value in md.items():
            if key not in merged and value:
                # Clean string values
                if isinstance(value, str):
                    value = html.unescape(value).strip()
                merged[key] = value

    return merged


def extract_article_content(html_content: str, url: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Extract main article content from HTML.
    Uses trafilatura as primary, with readability-lxml as fallback.

    Returns:
        Tuple of (markdown_content, metadata_dict)
    """
    content = None
    metadata = {}

    # Try trafilatura first (best for article extraction)
    if TRAFILATURA_AVAILABLE:
        try:
            # Extract metadata using trafilatura
            traf_metadata = extract_metadata(html_content)
            if traf_metadata:
                if traf_metadata.title:
                    metadata['title'] = traf_metadata.title
                if traf_metadata.author:
                    metadata['author'] = traf_metadata.author
                if traf_metadata.date:
                    metadata['publication_date'] = traf_metadata.date
                if traf_metadata.sitename:
                    metadata['source_name'] = traf_metadata.sitename
                if traf_metadata.description:
                    metadata['description'] = traf_metadata.description

            # Extract content as markdown
            content = extract(
                html_content,
                output_format='markdown',
                include_links=True,
                include_images=True,
                include_tables=True,
                include_formatting=True,
                favor_precision=True,
                deduplicate=True,
            )

            if content:
                return content, metadata

        except Exception as e:
            print(f"Trafilatura extraction failed: {e}")

    # Fallback to readability-lxml
    if READABILITY_AVAILABLE and not content:
        try:
            doc = Document(html_content)
            title = doc.title()
            html_content_clean = doc.summary()

            if title:
                metadata['title'] = title

            # Convert HTML to simple markdown
            content = html_to_simple_markdown(html_content_clean)

            if content:
                return content, metadata

        except Exception as e:
            print(f"Readability extraction failed: {e}")

    # Last resort: basic BeautifulSoup extraction
    if BS4_AVAILABLE and not content:
        try:
            soup = BeautifulSoup(html_content, 'html.parser')

            # Remove unwanted elements
            for tag in soup.find_all(['nav', 'header', 'footer', 'aside', 'script',
                                       'style', 'noscript', 'iframe', 'form']):
                tag.decompose()

            # Try to find article content
            article = soup.find('article') or soup.find('main') or soup.find('div', class_=re.compile(r'content|post|article', re.I))

            if article:
                content = html_to_simple_markdown(str(article))
            else:
                # Get body content
                body = soup.find('body')
                if body:
                    content = html_to_simple_markdown(str(body))

        except Exception as e:
            print(f"BeautifulSoup extraction failed: {e}")

    return content, metadata


def html_to_simple_markdown(html_content: str) -> str:
    """Convert HTML to simple markdown (basic implementation)."""
    if not BS4_AVAILABLE:
        # Very basic regex-based conversion
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.I)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.I)
        text = re.sub(r'<[^>]+>', '', text)
        text = html.unescape(text)
        return text.strip()

    soup = BeautifulSoup(html_content, 'html.parser')

    # Process headings
    for i in range(1, 7):
        for heading in soup.find_all(f'h{i}'):
            text = heading.get_text(strip=True)
            heading.replace_with(f"\n\n{'#' * i} {text}\n\n")

    # Process paragraphs
    for p in soup.find_all('p'):
        text = p.get_text(strip=True)
        p.replace_with(f"\n\n{text}\n\n")

    # Process bold
    for b in soup.find_all(['strong', 'b']):
        text = b.get_text(strip=True)
        b.replace_with(f"**{text}**")

    # Process italic
    for i in soup.find_all(['em', 'i']):
        text = i.get_text(strip=True)
        i.replace_with(f"*{text}*")

    # Process links
    for a in soup.find_all('a'):
        text = a.get_text(strip=True)
        href = a.get('href', '')
        if href and text:
            a.replace_with(f"[{text}]({href})")
        elif text:
            a.replace_with(text)

    # Process lists
    for ul in soup.find_all('ul'):
        items = []
        for li in ul.find_all('li', recursive=False):
            items.append(f"- {li.get_text(strip=True)}")
        ul.replace_with('\n' + '\n'.join(items) + '\n')

    for ol in soup.find_all('ol'):
        items = []
        for idx, li in enumerate(ol.find_all('li', recursive=False), 1):
            items.append(f"{idx}. {li.get_text(strip=True)}")
        ol.replace_with('\n' + '\n'.join(items) + '\n')

    # Process blockquotes
    for bq in soup.find_all('blockquote'):
        text = bq.get_text(strip=True)
        lines = text.split('\n')
        quoted = '\n'.join(f"> {line}" for line in lines)
        bq.replace_with(f"\n\n{quoted}\n\n")

    # Process code blocks
    for pre in soup.find_all('pre'):
        code = pre.find('code')
        if code:
            text = code.get_text()
            lang = ''
            if code.get('class'):
                for cls in code.get('class', []):
                    if cls.startswith('language-'):
                        lang = cls.replace('language-', '')
                        break
            pre.replace_with(f"\n\n```{lang}\n{text}\n```\n\n")
        else:
            text = pre.get_text()
            pre.replace_with(f"\n\n```\n{text}\n```\n\n")

    # Process inline code
    for code in soup.find_all('code'):
        if code.parent and code.parent.name != 'pre':
            text = code.get_text(strip=True)
            code.replace_with(f"`{text}`")

    # Get text and clean up
    text = soup.get_text()
    text = html.unescape(text)

    # Clean up whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)

    return text.strip()


def extract_images(html_content: str, base_url: str) -> List[Dict[str, str]]:
    """Extract image information from HTML content."""
    images = []

    if not BS4_AVAILABLE:
        return images

    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if not src:
                continue

            # Make URL absolute
            if not src.startswith(('http://', 'https://', 'data:')):
                src = urljoin(base_url, src)

            # Skip data URLs and tracking pixels
            if src.startswith('data:') or '1x1' in src or 'pixel' in src.lower():
                continue

            alt = img.get('alt', '')
            title = img.get('title', '')

            # Skip tiny images (likely icons)
            width = img.get('width', '')
            height = img.get('height', '')
            try:
                if width and int(width) < 50:
                    continue
                if height and int(height) < 50:
                    continue
            except ValueError:
                pass

            images.append({
                'url': src,
                'alt': alt,
                'title': title or alt,
            })

    except Exception:
        pass

    return images


def download_image(image_url: str, output_dir: Path, base_name: str, index: int) -> Tuple[Optional[str], Optional[str]]:
    """
    Download an image and save it locally.

    Returns:
        Tuple of (local_path, error_message)
    """
    if not REQUESTS_AVAILABLE:
        return None, "requests library not available"

    try:
        response = requests.get(
            image_url,
            headers=DEFAULT_HEADERS,
            timeout=30,
            stream=True
        )
        response.raise_for_status()

        # Determine file extension
        content_type = response.headers.get('content-type', '')
        ext = mimetypes.guess_extension(content_type.split(';')[0]) or '.jpg'
        if ext == '.jpe':
            ext = '.jpg'

        # Create filename
        filename = f"{base_name} - Figure {index}{ext}"
        filepath = output_dir / filename

        # Download
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        return filename, None

    except Exception as e:
        return None, str(e)


def calculate_reading_time(text: str) -> int:
    """Calculate estimated reading time in minutes."""
    # Average reading speed: 200-250 words per minute
    # Using 225 as a middle ground
    words = len(text.split())
    minutes = max(1, round(words / 225))
    return minutes


def sanitize_filename(text: str) -> str:
    """Sanitize text for use in filename."""
    if not text:
        return ""

    # Replace problematic characters
    text = re.sub(r'[<>:"/\\|?*]', '', text)
    # Replace colons and other separators with dash
    text = re.sub(r'[:;]', ' -', text)
    # Replace multiple spaces/underscores with single space
    text = re.sub(r'[\s_]+', ' ', text)
    # Remove leading/trailing spaces
    text = text.strip()
    # Limit length
    if len(text) > 80:
        text = text[:80].rsplit(' ', 1)[0]

    return text


def create_output_filename(metadata: Dict[str, Any], url: str) -> str:
    """
    Create output filename in format: Author - Title - Source.md
    """
    parts = []

    # Author
    author = sanitize_filename(metadata.get('author', ''))
    if author:
        parts.append(author)
    else:
        parts.append('Unknown Author')

    # Title
    title = sanitize_filename(metadata.get('title', ''))
    if title:
        parts.append(title)
    else:
        # Use URL path as fallback
        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')
        if path_parts:
            parts.append(sanitize_filename(path_parts[-1].replace('-', ' ').replace('_', ' ').title()))
        else:
            parts.append('Untitled')

    # Source
    source = sanitize_filename(metadata.get('source_name', ''))
    if not source:
        parsed = urlparse(url)
        source = parsed.netloc.replace('www.', '').split('.')[0].capitalize()
    parts.append(source)

    return ' - '.join(parts) + '.md'


def format_date(date_str: Optional[str]) -> Optional[str]:
    """Format date string to YYYY-MM-DD format."""
    if not date_str:
        return None

    # Common date formats to try
    formats = [
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d',
        '%B %d, %Y',
        '%b %d, %Y',
        '%d %B %Y',
        '%d %b %Y',
        '%m/%d/%Y',
        '%d/%m/%Y',
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue

    # Try to extract just a date with regex
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
    if match:
        return match.group(0)

    return date_str  # Return as-is if we can't parse it


def generate_yaml_frontmatter(
    metadata: Dict[str, Any],
    url: str,
    reading_time: int,
    tags: List[str] = None,
    has_toc: bool = False
) -> str:
    """Generate YAML frontmatter for the markdown file."""
    lines = ['---']

    # Title
    title = metadata.get('title', 'Untitled')
    # Escape quotes in title
    title = title.replace('"', '\\"')
    lines.append(f'title: "{title}"')

    # Author
    author = metadata.get('author', 'Unknown')
    author = author.replace('"', '\\"')
    lines.append(f'author: "{author}"')

    # Source information
    source_name = metadata.get('source_name', urlparse(url).netloc.replace('www.', ''))
    lines.append(f'source_name: "{source_name}"')
    lines.append(f'source_url: "{url}"')

    # Dates
    pub_date = format_date(metadata.get('publication_date'))
    if pub_date:
        lines.append(f'publication_date: "{pub_date}"')

    lines.append(f'retrieved_date: "{datetime.now().strftime("%Y-%m-%d")}"')

    # Reading time
    lines.append(f'reading_time_minutes: {reading_time}')

    # Tags/topics if available
    if tags:
        # Format as YAML list
        lines.append('tags:')
        for tag in tags:
            # Escape quotes in tags
            tag = tag.replace('"', '\\"')
            lines.append(f'  - "{tag}"')

    # Description/summary if available (from author, not generated)
    description = metadata.get('description')
    if description:
        # Escape quotes in description
        description = description.replace('"', '\\"')
        # Truncate if too long
        if len(description) > 300:
            description = description[:297] + '...'
        lines.append(f'description: "{description}"')

    # Indicate if TOC is included
    if has_toc:
        lines.append('has_toc: true')

    # Content type and version
    lines.append('content_type: "web_article"')
    lines.append(f'converter_version: "{CONVERTER_VERSION}"')

    lines.append('---')

    return '\n'.join(lines)


def clean_markdown_for_rag(content: str) -> str:
    """Clean and optimize markdown content for RAG systems."""

    # Remove excessive blank lines
    content = re.sub(r'\n{3,}', '\n\n', content)

    # Remove trailing whitespace from lines
    content = '\n'.join(line.rstrip() for line in content.split('\n'))

    # Fix heading spacing (ensure blank line before headings)
    content = re.sub(r'([^\n])\n(#{1,6}\s)', r'\1\n\n\2', content)

    # Remove empty headings
    content = re.sub(r'^#{1,6}\s*$', '', content, flags=re.MULTILINE)

    # Clean up link artifacts
    content = re.sub(r'\[([^\]]+)\]\(\s*\)', r'\1', content)  # Empty links
    content = re.sub(r'\[([^\]]+)\]\(#[^\)]*\)', r'\1', content)  # Anchor-only links

    # Remove image placeholders with no real content
    content = re.sub(r'!\[\s*\]\([^\)]+\)', '', content)

    # Clean up any remaining HTML entities
    content = html.unescape(content)

    # Ensure file ends with single newline
    content = content.strip() + '\n'

    return content


def convert_url_to_markdown(
    url: str,
    output_dir: str,
    download_images: bool = True,
    image_subdir: str = 'article_images'
) -> Tuple[bool, str, Optional[str]]:
    """
    Convert a web URL to an AI-optimized Markdown file.

    Args:
        url: The URL to convert
        output_dir: Directory to save the output file
        download_images: Whether to download images
        image_subdir: Subdirectory name for images

    Returns:
        Tuple of (success, message, output_filepath)
    """
    # Check dependencies
    deps_ok, missing = check_dependencies()
    if not deps_ok:
        return False, f"Missing required dependencies: {', '.join(missing)}. Install with: pip install {' '.join(missing)}", None

    print(f"\n{'='*60}")
    print(f"Converting: {url}")
    print('='*60)

    # Step 1: Fetch the URL
    print("\n[1/6] Fetching URL...")
    html_content, error = fetch_url(url)
    if error:
        return False, f"Failed to fetch URL: {error}", None

    print(f"      Fetched {len(html_content):,} bytes")

    # Step 2: Extract metadata from multiple sources
    print("\n[2/6] Extracting metadata...")
    json_ld_meta = extract_json_ld_metadata(html_content)
    og_meta = extract_opengraph_metadata(html_content)
    html_meta = extract_html_metadata(html_content, url)

    # Merge metadata (priority: JSON-LD > OpenGraph > HTML)
    metadata = merge_metadata(json_ld_meta, og_meta, html_meta)

    # Extract tags/topics
    tags = extract_tags_and_topics(html_content)

    # Extract table of contents
    toc = extract_table_of_contents(html_content)

    print(f"      Title: {metadata.get('title', 'Not found')}")
    print(f"      Author: {metadata.get('author', 'Not found')}")
    print(f"      Date: {metadata.get('publication_date', 'Not found')}")
    print(f"      Source: {metadata.get('source_name', 'Not found')}")
    if tags:
        print(f"      Tags: {', '.join(tags[:5])}{'...' if len(tags) > 5 else ''}")
    if toc:
        print(f"      TOC: {len(toc)} sections found")

    # Step 3: Extract article content
    print("\n[3/6] Extracting article content...")
    content, content_meta = extract_article_content(html_content, url)

    if not content:
        return False, "Failed to extract article content", None

    # Merge any additional metadata from content extraction
    metadata = merge_metadata(metadata, content_meta)

    print(f"      Extracted {len(content):,} characters")

    # Step 4: Handle images
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if download_images:
        print("\n[4/6] Processing images...")
        images = extract_images(html_content, url)
        print(f"      Found {len(images)} images")

        if images:
            image_dir = output_path / image_subdir
            image_dir.mkdir(exist_ok=True)

            # Create base name for images
            base_name = sanitize_filename(f"{metadata.get('author', 'Unknown')} - {metadata.get('title', 'Article')[:40]} - {metadata.get('source_name', 'Web')}")

            downloaded = 0
            for idx, img in enumerate(images, 1):
                local_name, err = download_image(img['url'], image_dir, base_name, idx)
                if local_name:
                    downloaded += 1
                    # Update content to reference local image
                    old_ref = img['url']
                    new_ref = f"{image_subdir}/{local_name}"
                    alt_text = img['alt'] or img['title'] or f"Figure {idx}"

                    # Try to replace the image reference in content
                    # This handles various markdown image formats
                    content = content.replace(f"]({old_ref})", f"]({new_ref})")
                    content = content.replace(f"src=\"{old_ref}\"", f"src=\"{new_ref}\"")

            print(f"      Downloaded {downloaded}/{len(images)} images")
    else:
        print("\n[4/6] Skipping image download...")

    # Step 5: Clean and optimize content
    print("\n[5/6] Cleaning content for RAG...")
    content = clean_markdown_for_rag(content)

    # Calculate reading time
    reading_time = calculate_reading_time(content)
    print(f"      Estimated reading time: {reading_time} minutes")

    # Step 6: Generate final output
    print("\n[6/6] Generating output file...")

    # Generate YAML frontmatter (with tags and TOC indicator)
    has_toc = len(toc) >= 3  # Only include TOC if we have 3+ sections
    frontmatter = generate_yaml_frontmatter(metadata, url, reading_time, tags, has_toc)

    # Build final content
    content_parts = [frontmatter, ""]

    # Add table of contents if available (3+ sections)
    if has_toc:
        toc_markdown = format_toc_for_markdown(toc, metadata.get('title'))
        content_parts.append(toc_markdown)
        content_parts.append("")

    content_parts.append(content)

    # Combine all parts
    final_content = "\n".join(content_parts)

    # Generate filename
    filename = create_output_filename(metadata, url)
    filepath = output_path / filename

    # Write file
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(final_content)

    file_size = filepath.stat().st_size / 1024
    print(f"\n{'='*60}")
    print(f"SUCCESS!")
    print(f"Output: {filepath}")
    print(f"Size: {file_size:.1f} KB")
    print('='*60)

    return True, f"Successfully converted to: {filename}", str(filepath)


def main():
    """Main entry point for CLI usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Convert web articles to AI-optimized Markdown',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python html_to_md_converter.py https://example.com/article
  python html_to_md_converter.py https://example.com/article -o ./output
  python html_to_md_converter.py https://example.com/article --no-images
        """
    )

    parser.add_argument('url', help='URL of the article to convert')
    parser.add_argument('-o', '--output', default='./converted_articles',
                        help='Output directory (default: ./converted_articles)')
    parser.add_argument('--no-images', action='store_true',
                        help='Skip downloading images')
    parser.add_argument('--image-dir', default='article_images',
                        help='Subdirectory for images (default: article_images)')

    args = parser.parse_args()

    success, message, filepath = convert_url_to_markdown(
        url=args.url,
        output_dir=args.output,
        download_images=not args.no_images,
        image_subdir=args.image_dir
    )

    if success:
        print(f"\n{message}")
        sys.exit(0)
    else:
        print(f"\nError: {message}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
