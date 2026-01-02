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
import time
import hashlib
import mimetypes
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Any
from datetime import datetime
from urllib.parse import urlparse, urljoin
import html

# Script version for tracking conversions
CONVERTER_VERSION = "1.0.17"  # 1.0.17 refactors Medium support into separate module

# Try to import required libraries
TRAFILATURA_AVAILABLE = False
READABILITY_AVAILABLE = False
REQUESTS_AVAILABLE = False
BS4_AVAILABLE = False
SELENIUM_AVAILABLE = False

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

# ============================================================================
# OPTIONAL: Medium Article Support (via Selenium)
# Import from separate module for feature flagging and cleaner architecture
# ============================================================================
MEDIUM_SUPPORT_AVAILABLE = False
is_medium_url = None
fetch_medium_with_selenium = None

try:
    from medium_scraper import (
        is_medium_url,
        fetch_medium_with_selenium,
        MEDIUM_SUPPORT_AVAILABLE,
        SELENIUM_AVAILABLE
    )
except ImportError:
    # Medium support not available - that's OK, core functionality still works
    SELENIUM_AVAILABLE = False

    def is_medium_url(url: str) -> bool:
        """Stub: Medium support not available."""
        return False

    def fetch_medium_with_selenium(url: str):
        """Stub: Medium support not available."""
        return None, "Medium support not installed. Run: pip install selenium webdriver-manager undetected-chromedriver"


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


def sanitize_html(html_content: str) -> str:
    """Remove control characters and other problematic content from HTML."""
    if not html_content:
        return html_content

    # Remove NULL bytes and control characters (except newline, tab, carriage return)
    # Control chars are 0x00-0x1F and 0x7F-0x9F, but keep \t (0x09), \n (0x0A), \r (0x0D)
    cleaned = []
    for char in html_content:
        code = ord(char)
        if code == 0x09 or code == 0x0A or code == 0x0D:  # tab, newline, carriage return
            cleaned.append(char)
        elif code < 0x20 or (0x7F <= code <= 0x9F):  # control characters
            cleaned.append(' ')  # Replace with space
        else:
            cleaned.append(char)

    return ''.join(cleaned)


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

        # Sanitize HTML to remove control characters
        content = sanitize_html(response.text)

        return content, None

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


def extract_toc_from_markdown(markdown_content: str) -> List[Dict[str, Any]]:
    """
    Extract table of contents from markdown headings.
    This is more reliable than extracting from HTML as it works on the final cleaned content.
    """
    toc = []

    # Match markdown headings: ## Heading or ### Heading etc.
    heading_pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)

    for match in heading_pattern.finditer(markdown_content):
        level = len(match.group(1))
        text = match.group(2).strip()

        # Skip empty or very short headings
        if not text or len(text) < 2:
            continue

        # Skip headings that look like marketing/promotional content
        marketing_keywords = [
            'subscribe', 'newsletter', 'sign up', 'follow us', 'related',
            'more from', 'you might', 'recommended', 'share this',
            'about the author', 'join our', 'connect with'
        ]
        if any(kw in text.lower() for kw in marketing_keywords):
            continue

        toc.append({'text': text, 'level': level})

    return toc


def extract_spa_metadata(html_content: str, url: str) -> Dict[str, Any]:
    """
    Extract metadata from modern SPA (Single Page Application) sites.
    Handles sites like Heavybit, Medium, Substack that use React/Vue/etc.
    """
    metadata = {}

    if not BS4_AVAILABLE:
        return metadata

    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        # ===== TITLE: Look for h1 with specific patterns =====
        h1 = soup.find('h1')
        if h1:
            # Handle nested spans (common in React apps for text balancing)
            span = h1.find('span', {'data-br': True}) or h1.find('span')
            if span:
                metadata['title'] = span.get_text(strip=True)
            else:
                metadata['title'] = h1.get_text(strip=True)

        # ===== AUTHOR: Look for author card patterns =====
        # Medium-specific: Look for "Written by [Name]" pattern
        # This is needed because Medium's article:author meta tag contains a URL, not the display name
        if is_medium_url(url):
            # Pattern A: Find "Written by" text and get the name from the SAME parent container
            written_by = soup.find(string=re.compile(r'Written by', re.I))
            if written_by:
                # Get the immediate parent and look for the name within it
                parent = written_by.find_parent()
                if parent:
                    # Go up to find the author card container (usually a div or section)
                    author_card = parent.find_parent(['div', 'section', 'li'])
                    if author_card:
                        # Look for links that contain the author name (usually the first meaningful link)
                        for link in author_card.find_all('a', href=True):
                            href = link.get('href', '')
                            # Skip links to publications, tags, or the article itself
                            if '/tag/' in href or 'subscribe' in href.lower():
                                continue
                            text = link.get_text(strip=True)
                            # Check if this looks like a person name (at least 2 words, starts with capital)
                            if text and len(text) > 2 and len(text) < 50:
                                # Match names like "Mikael Cho", "John Smith Jr", etc.
                                if re.match(r'^[A-Z][a-z]+\s+[A-Z]', text):
                                    # This is likely the author name
                                    metadata['author'] = text
                                    break

            # Pattern B: Look for the author section with avatar and "Written by" text together
            if 'author' not in metadata:
                # Find all divs/sections that contain "Written by"
                for container in soup.find_all(['div', 'section']):
                    container_text = container.get_text()
                    if 'Written by' in container_text:
                        # Look for an h4 or strong link inside this container (common Medium pattern)
                        for name_elem in container.find_all(['h4', 'a']):
                            name_text = name_elem.get_text(strip=True)
                            if name_text and len(name_text) > 2 and len(name_text) < 50:
                                # Check it's a person name (starts with First Last pattern)
                                if re.match(r'^[A-Z][a-z]+\s+[A-Z][a-z]+', name_text):
                                    # Make sure this is in the same "Written by" section
                                    if 'Written by' in container_text[:200]:
                                        metadata['author'] = name_text
                                        break
                        if 'author' in metadata:
                            break

        # Pattern 1: Image with "Photo" in alt + nearby name
        author_img = soup.find('img', alt=re.compile(r'Photo|Avatar|Author', re.I))
        if author_img:
            # Look in parent containers for author name
            parent = author_img.find_parent('li') or author_img.find_parent('div') or author_img.find_parent('a')
            if parent:
                # Find text elements that look like names (not "Photo of...")
                for elem in parent.find_all(['span', 'a', 'p', 'div']):
                    text = elem.get_text(strip=True)
                    # Skip if it's just the photo alt or too short/long
                    if text and len(text) > 2 and len(text) < 50:
                        if not re.match(r'^(Photo|Avatar|Image|By|Written)', text, re.I):
                            # Looks like a name - check if it has name-like characteristics
                            if re.match(r'^[A-Z][a-z]+ [A-Z]', text) or ' ' in text:
                                metadata['author'] = text
                                break

        # Pattern 2: Look for elements with author-related classes
        if 'author' not in metadata:
            author_patterns = [
                ('*', {'class': re.compile(r'author-name|authorName|author__name|byline-name', re.I)}),
                ('a', {'href': re.compile(r'/author/|/team/', re.I)}),
            ]
            for tag, attrs in author_patterns:
                elem = soup.find(tag, attrs)
                if elem:
                    text = elem.get_text(strip=True)
                    text = re.sub(r'^(by|written by|author:?)\s*', '', text, flags=re.I)
                    if text and len(text) > 2 and len(text) < 60:
                        metadata['author'] = text
                        break

        # ===== DATE: time element with datetime attribute =====
        time_elem = soup.find('time', datetime=True)
        if time_elem:
            metadata['publication_date'] = time_elem.get('datetime')

        # ===== READING TIME: Look for reading time near article header =====
        reading_time_found = None

        # Pattern 1: Look for explicit "X min read" pattern (most reliable)
        read_pattern = soup.find(string=re.compile(r'\b(\d+)\s*min(ute)?s?\s*read\b', re.I))
        if read_pattern:
            match = re.search(r'(\d+)\s*min', read_pattern, re.I)
            if match:
                rt = int(match.group(1))
                if 1 <= rt <= 60:  # Reasonable article reading time
                    reading_time_found = rt

        # Pattern 2: Look for standalone "X min" near h1/header area
        if not reading_time_found and h1:
            # Search in multiple parent levels
            for parent_tag in ['li', 'div', 'section', 'header', 'article']:
                header_area = h1.find_parent(parent_tag)
                if header_area:
                    # Look for any element containing just "X min"
                    for elem in header_area.find_all(['span', 'div', 'p', 'time']):
                        text = elem.get_text(strip=True)
                        # Match "9 min" or "9min" standalone
                        if re.match(r'^\d+\s*min(ute)?s?\.?$', text, re.I):
                            match = re.search(r'(\d+)', text)
                            if match:
                                rt = int(match.group(1))
                                if 1 <= rt <= 60:
                                    reading_time_found = rt
                                    break
                    if reading_time_found:
                        break

        # Pattern 3: Search entire page for "X min" in small text elements (likely metadata)
        if not reading_time_found:
            for elem in soup.find_all(['span', 'div', 'p'], string=re.compile(r'^\d+\s*min\.?$', re.I)):
                text = elem.get_text(strip=True)
                match = re.search(r'(\d+)', text)
                if match:
                    rt = int(match.group(1))
                    if 1 <= rt <= 60:
                        reading_time_found = rt
                        break

        if reading_time_found:
            metadata['reading_time_raw'] = reading_time_found

        # ===== TAGS: Look for tag/topic links =====
        tags = []

        # Helper to check if text is a valid tag
        def is_valid_tag(text):
            if not text or len(text) <= 2 or len(text) >= 50:
                return False
            # Skip navigation-like text
            if re.match(r'^(home|about|contact|blog|all|more|see|view|read|share|library)', text, re.I):
                return False
            # Skip if it looks like a sentence
            if len(text.split()) > 4:
                return False
            return True

        # Pattern 1: Links with /topic/, /tag/, /category/, /subject/ in href
        # Also check for /library/topic/ (Heavybit pattern)
        topic_links = soup.find_all('a', href=re.compile(r'/(topic|tag|category|subject|subjects)/', re.I))
        for link in topic_links:
            tag_text = link.get_text(strip=True)
            if is_valid_tag(tag_text) and tag_text not in tags:
                tags.append(tag_text)

        # Pattern 2: Elements with tag/topic class containing links
        tag_containers = soup.find_all(['ul', 'div', 'nav', 'section'], class_=re.compile(r'tag|topic|categor|subject', re.I))
        for container in tag_containers:
            for tag_link in container.find_all('a'):
                tag_text = tag_link.get_text(strip=True)
                if is_valid_tag(tag_text) and tag_text not in tags:
                    tags.append(tag_text)

        # Pattern 3: Look for "Topics:" or "Tags:" labels followed by links
        for label in soup.find_all(string=re.compile(r'^(topics?|tags?|categories?|subjects?):\s*$', re.I)):
            parent = label.find_parent()
            if parent:
                for link in parent.find_all('a'):
                    tag_text = link.get_text(strip=True)
                    if is_valid_tag(tag_text) and tag_text not in tags:
                        tags.append(tag_text)

        # Pattern 4: Look for article:tag meta tags
        for meta in soup.find_all('meta', property='article:tag'):
            tag_text = meta.get('content', '').strip()
            if is_valid_tag(tag_text) and tag_text not in tags:
                tags.append(tag_text)

        # Pattern 5: Look for keywords meta tag
        keywords_meta = soup.find('meta', attrs={'name': 'keywords'})
        if keywords_meta and keywords_meta.get('content'):
            for kw in keywords_meta['content'].split(','):
                kw = kw.strip()
                if is_valid_tag(kw) and kw not in tags:
                    tags.append(kw)

        # Pattern 6: Look for ?query= URL patterns (Heavybit-style topic links)
        # e.g., /library?query=Hiring, /library?query=Product%20Management
        query_link_pattern = re.compile(r'\?query=', re.I)

        # Navigation-style phrases to skip (not tags)
        nav_phrase_pattern = re.compile(
            r'^(visit|go\s*to|back\s*to|view|see|read|explore|browse|return\s*to)\s',
            re.I
        )
        skip_link_texts = ['all', 'more', 'view all', 'see all', 'browse', 'search', 'library']

        for a_tag in soup.find_all('a', href=True):
            href = a_tag.get('href', '')
            if query_link_pattern.search(href):
                tag_text = a_tag.get_text(strip=True)
                # Validate: reasonable length, not navigation text, not a URL
                if (tag_text and
                    2 < len(tag_text) < 40 and
                    tag_text.lower() not in skip_link_texts and
                    not tag_text.startswith('http') and
                    not nav_phrase_pattern.match(tag_text) and
                    tag_text not in tags):
                    tags.append(tag_text)

        if tags:
            metadata['tags'] = tags[:15]  # Limit to 15 tags

    except Exception as e:
        print(f"      Warning: SPA metadata extraction error: {e}")

    return metadata


def extract_html_metadata(html_content: str, url: str) -> Dict[str, Any]:
    """Extract metadata by parsing HTML structure."""
    metadata = {}

    if not BS4_AVAILABLE:
        return metadata

    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        # ===== TITLE EXTRACTION =====
        # Priority 1: h1 tag (usually the main title)
        h1_tag = soup.find('h1')
        if h1_tag:
            h1_text = h1_tag.get_text(strip=True)
            if h1_text and len(h1_text) > 5 and len(h1_text) < 300:
                metadata['title'] = h1_text

        # Priority 2: Title from <title> tag (fallback)
        if 'title' not in metadata:
            title_tag = soup.find('title')
            if title_tag:
                title_text = title_tag.get_text(strip=True)
                if title_text:
                    # Clean up title (often includes site name)
                    for sep in [' | ', ' - ', ' — ', ' :: ', ' // ', ' · ']:
                        if sep in title_text:
                            parts = title_text.split(sep)
                            # Usually the article title is the first or longest part
                            title_text = max(parts, key=len).strip()
                            break
                    metadata['title'] = title_text

        # ===== AUTHOR EXTRACTION =====
        author_selectors = [
            # Meta tags
            ('meta', {'name': 'author'}),
            ('meta', {'property': 'author'}),
            ('meta', {'name': 'article:author'}),
            # Rel author
            ('a', {'rel': 'author'}),
            # Class-based selectors (common patterns)
            ('span', {'class': re.compile(r'author-name|authorName|author__name', re.I)}),
            ('a', {'class': re.compile(r'author-name|authorName|author__name', re.I)}),
            ('div', {'class': re.compile(r'author-name|authorName|author__name', re.I)}),
            ('span', {'class': re.compile(r'^author$|byline-author', re.I)}),
            ('div', {'class': re.compile(r'^author$|byline-author', re.I)}),
            ('p', {'class': re.compile(r'^author$|byline', re.I)}),
            # Data attributes
            ('*', {'data-author': True}),
            # Itemprop
            ('*', {'itemprop': 'author'}),
            ('*', {'itemprop': 'name', 'itemtype': re.compile(r'Person', re.I)}),
        ]

        for tag, attrs in author_selectors:
            if 'author' not in metadata:
                elem = soup.find(tag, attrs)
                if elem:
                    if tag == 'meta':
                        author = elem.get('content', '')
                    elif 'data-author' in attrs:
                        author = elem.get('data-author', '')
                    else:
                        # Get text content
                        author = elem.get_text(strip=True)

                    # Clean up common prefixes
                    author = re.sub(r'^(by|written by|author:?|posted by)\s*', '', author, flags=re.I)
                    author = author.strip()

                    if author and len(author) > 1 and len(author) < 100:
                        metadata['author'] = author

        # ===== DATE EXTRACTION =====
        date_selectors = [
            # Time elements
            ('time', {'datetime': True}),
            ('time', {}),
            # Meta tags
            ('meta', {'property': 'article:published_time'}),
            ('meta', {'name': 'date'}),
            ('meta', {'name': 'publish_date'}),
            ('meta', {'name': 'pubdate'}),
            ('meta', {'itemprop': 'datePublished'}),
            # Class-based
            ('span', {'class': re.compile(r'date|publish|posted', re.I)}),
            ('div', {'class': re.compile(r'date|publish|posted', re.I)}),
            ('p', {'class': re.compile(r'date|publish|posted', re.I)}),
            # Itemprop
            ('*', {'itemprop': 'datePublished'}),
        ]

        for tag, attrs in date_selectors:
            if 'publication_date' not in metadata:
                elem = soup.find(tag, attrs)
                if elem:
                    if tag == 'time':
                        date_val = elem.get('datetime') or elem.get_text(strip=True)
                    elif tag == 'meta':
                        date_val = elem.get('content', '')
                    else:
                        date_val = elem.get('datetime') or elem.get_text(strip=True)

                    if date_val:
                        metadata['publication_date'] = date_val.strip()

        # ===== SOURCE NAME =====
        if 'source_name' not in metadata:
            # Try og:site_name first
            og_site = soup.find('meta', property='og:site_name')
            if og_site and og_site.get('content'):
                metadata['source_name'] = og_site['content']
            else:
                # Fall back to domain
                parsed = urlparse(url)
                domain = parsed.netloc.replace('www.', '')
                parts = domain.split('.')
                if parts:
                    metadata['source_name'] = parts[0].capitalize()

    except Exception as e:
        print(f"      Warning: HTML metadata extraction error: {e}")

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


def is_content_valid(content: str) -> bool:
    """Check if extracted content is valid (not garbage/corrupted)."""
    if not content or len(content) < 100:
        return False

    # Check for high ratio of printable characters
    sample = content[:2000]
    printable_count = sum(1 for c in sample if c.isprintable() or c in '\n\r\t')
    printable_ratio = printable_count / len(sample)

    # Lowered threshold - SPAs might have some encoded characters
    if printable_ratio < 0.75:
        return False

    # Check for actual words (not just random characters)
    # Lowered from 20 to 10 to allow shorter content
    words = re.findall(r'\b[a-zA-Z]{3,}\b', sample)
    if len(words) < 10:
        return False

    return True


def preprocess_medium_html(html_content: str) -> str:
    """
    Preprocess Medium HTML to remove responses/comments section.
    Medium includes user comments in the page HTML which gets extracted as article content.
    """
    if not BS4_AVAILABLE:
        return html_content

    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        # Medium responses section patterns
        # Pattern 1: Section containing "Responses" text anywhere
        for elem in soup.find_all(['section', 'div']):
            text = elem.get_text() if elem.get_text() else ''
            # Check for "Responses (X)" pattern
            if re.search(r'Responses?\s*\(\d+\)', text[:500]):
                elem.decompose()
                continue

            # Check for class names related to responses/comments
            elem_class = ' '.join(elem.get('class', []))
            if re.search(r'response|comment|replies', elem_class, re.I):
                elem.decompose()

        # Pattern 2: Remove elements with specific response-related attributes
        for elem in soup.find_all(attrs={'data-testid': re.compile(r'response|comment', re.I)}):
            elem.decompose()

        # Pattern 3: Remove promotional CTA sections (common before responses)
        # These contain text like "Subscribe here" or "million people have used/read"
        for elem in soup.find_all(['div', 'section', 'p']):
            text = elem.get_text() if elem.get_text() else ''
            # Check for promotional stats patterns
            if re.search(r'(million|thousand)\s+people\s+have\s+(used|read)', text, re.I):
                # This is likely a promotional CTA - remove it and all following siblings
                for sibling in list(elem.find_next_siblings()):
                    sibling.decompose()
                elem.decompose()
                break
            # Check for "Subscribe here" CTA
            if re.search(r'Subscribe\s+here\.?\s*$', text, re.I):
                for sibling in list(elem.find_next_siblings()):
                    sibling.decompose()
                elem.decompose()
                break

        # Pattern 4: Remove "Written by" footer section that appears after article
        for elem in soup.find_all(['div', 'section']):
            elem_class = ' '.join(elem.get('class', []))
            if re.search(r'postMeta|authorCard|writer-card', elem_class, re.I):
                text = elem.get_text()
                if 'followers' in text.lower() and 'following' in text.lower():
                    elem.decompose()

        # Pattern 5: Remove elements that look like comments (contain common comment phrases)
        comment_patterns = [
            r'^BRAVO!',
            r'^Well said',
            r'^Thank [yY]ou',
            r'^I (very much )?(appreciate|agree)',
        ]
        for elem in soup.find_all(['p', 'div']):
            text = elem.get_text(strip=True) if elem.get_text() else ''
            for pattern in comment_patterns:
                if re.match(pattern, text):
                    elem.decompose()
                    break

        return str(soup)

    except Exception as e:
        print(f"      Warning: Medium HTML preprocessing error: {e}")
        return html_content


def extract_article_content(html_content: str, url: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Extract main article content from HTML.
    Uses trafilatura as primary, with readability-lxml and BeautifulSoup as fallbacks.
    Includes validation to detect corrupted/garbage output from SPA sites.

    Returns:
        Tuple of (markdown_content, metadata_dict)
    """
    # Preprocess Medium HTML to remove responses/comments
    if is_medium_url(url):
        html_content = preprocess_medium_html(html_content)

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

            # Try extraction with progressively simpler parameters
            # (trafilatura API varies between versions)
            content = None

            # Attempt 1: Full parameters with markdown
            print("      Trying trafilatura with markdown format...")
            try:
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
                    print(f"      Trafilatura markdown: got {len(content)} chars")
            except Exception as e:
                print(f"      Trafilatura markdown failed: {e}")
                content = None

            # Attempt 2: Simpler parameters (no output_format)
            if not content:
                print("      Trying trafilatura with simple parameters...")
                try:
                    content = extract(
                        html_content,
                        include_links=True,
                        include_tables=True,
                        deduplicate=True,
                    )
                    if content:
                        print(f"      Trafilatura simple: got {len(content)} chars")
                except Exception as e:
                    print(f"      Trafilatura simple failed: {e}")
                    content = None

            # Attempt 3: Minimal parameters
            if not content:
                print("      Trying trafilatura minimal...")
                try:
                    content = extract(html_content)
                    if content:
                        print(f"      Trafilatura minimal: got {len(content)} chars")
                except Exception as e:
                    print(f"      Trafilatura minimal failed: {e}")
                    content = None

            # Validate content isn't garbage (common with SPA sites)
            if content and not is_content_valid(content):
                print("      Warning: Extracted content appears corrupted")
                content = None

            if content and is_content_valid(content):
                return content, metadata

        except Exception as e:
            print(f"      Trafilatura extraction failed: {e}")

    # Fallback to readability-lxml
    if READABILITY_AVAILABLE and not content:
        print("      Trying readability-lxml fallback...")
        try:
            doc = Document(html_content)
            title = doc.title()
            html_content_clean = doc.summary()

            if title and 'title' not in metadata:
                metadata['title'] = title

            # Convert HTML to simple markdown
            content = html_to_simple_markdown(html_content_clean)

            if content and is_content_valid(content):
                print(f"      Readability: got {len(content)} chars")
                return content, metadata
            else:
                print("      Readability: content validation failed")
                content = None

        except Exception as e:
            print(f"      Readability extraction failed: {e}")

    # Last resort: Enhanced BeautifulSoup extraction for SPA sites
    if BS4_AVAILABLE and not content:
        print("      Trying BeautifulSoup fallback...")
        try:
            soup = BeautifulSoup(html_content, 'html.parser')

            # Remove unwanted elements (expanded list for SPAs)
            for tag in soup.find_all(['nav', 'header', 'footer', 'aside', 'script',
                                       'style', 'noscript', 'iframe', 'form', 'svg',
                                       'button', 'input', 'select', 'textarea']):
                tag.decompose()

            # Try multiple selectors in order of specificity
            article = None
            selectors = [
                ('div', {'id': 'content'}),
                ('div', {'id': 'article-content'}),
                ('div', {'class': re.compile(r'article-content|post-content|entry-content', re.I)}),
                ('article', {}),
                ('main', {}),
                ('div', {'class': re.compile(r'prose|markdown|rich-text', re.I)}),
                # Heavybit and similar SPA patterns
                ('div', {'class': re.compile(r'font-plexsans|article-body', re.I)}),
            ]

            for tag_name, attrs in selectors:
                found = soup.find(tag_name, attrs)
                if found:
                    # Verify it has substantial text content
                    text = found.get_text(strip=True)
                    if len(text) > 500:
                        print(f"      Found article via {tag_name} selector")
                        article = found
                        break

            # If no article found via selectors, try paragraph density detection
            if not article:
                print("      Trying paragraph density detection...")
                best_div = None
                best_p_count = 0

                for div in soup.find_all('div'):
                    paragraphs = div.find_all('p', recursive=True)
                    # Count paragraphs with substantial text
                    good_p = [p for p in paragraphs if len(p.get_text(strip=True)) > 50]
                    if len(good_p) > best_p_count:
                        best_p_count = len(good_p)
                        best_div = div

                if best_div and best_p_count >= 3:
                    print(f"      Found div with {best_p_count} substantial paragraphs")
                    article = best_div

            if article:
                content = html_to_simple_markdown(str(article))
            else:
                # Get body content as last resort
                print("      Using body content as last resort")
                body = soup.find('body')
                if body:
                    content = html_to_simple_markdown(str(body))

            # Final validation
            if content and not is_content_valid(content):
                print("      Warning: BeautifulSoup content also appears corrupted")
                content = None
            elif content:
                print(f"      BeautifulSoup: got {len(content)} chars")

        except Exception as e:
            print(f"      BeautifulSoup extraction failed: {e}")

    if not content:
        print("      ERROR: All extraction methods failed")

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
    """
    Extract image information from HTML content.
    Enhanced for SPA sites with og:image fallback and CDN handling.
    """
    images = []
    seen_urls = set()  # Track unique URLs to avoid duplicates

    if not BS4_AVAILABLE:
        return images

    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        # ===== Priority 1: OpenGraph image (often the best quality main image) =====
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            og_url = og_image['content']
            if og_url and og_url not in seen_urls:
                seen_urls.add(og_url)
                images.append({
                    'url': og_url,
                    'alt': 'Article main image',
                    'title': 'Main article image',
                    'priority': True
                })

        # ===== Priority 2: Twitter card image (backup for og:image) =====
        if not images:
            twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
            if twitter_image and twitter_image.get('content'):
                tw_url = twitter_image['content']
                if tw_url and tw_url not in seen_urls:
                    seen_urls.add(tw_url)
                    images.append({
                        'url': tw_url,
                        'alt': 'Article main image',
                        'title': 'Main article image',
                        'priority': True
                    })

        # ===== Find all img tags with extended attribute checking =====
        for img in soup.find_all('img'):
            # Check multiple src attributes (for lazy loading patterns)
            src = None
            for attr in ['src', 'data-src', 'data-lazy-src', 'data-original',
                        'data-srcset', 'data-full-src', 'data-image']:
                src = img.get(attr)
                if src and not src.startswith('data:'):
                    break

            # Handle srcset (take highest resolution)
            if not src and img.get('srcset'):
                srcset = img.get('srcset', '')
                # Parse srcset to find largest image
                srcset_parts = srcset.split(',')
                max_width = 0
                for part in srcset_parts:
                    part = part.strip()
                    if ' ' in part:
                        url_part, size_part = part.rsplit(' ', 1)
                        # Extract width from size (e.g., "800w" -> 800)
                        try:
                            width = int(re.sub(r'\D', '', size_part))
                            if width > max_width:
                                max_width = width
                                src = url_part.strip()
                        except ValueError:
                            pass
                    elif part and not part.startswith('data:'):
                        src = part

            if not src:
                continue

            # Make URL absolute
            if not src.startswith(('http://', 'https://', 'data:')):
                src = urljoin(base_url, src)

            # Skip data URLs and tracking pixels
            if src.startswith('data:') or '1x1' in src or 'pixel' in src.lower():
                continue

            # Skip if already seen
            if src in seen_urls:
                continue
            seen_urls.add(src)

            alt = img.get('alt', '')
            title = img.get('title', '')

            # Skip tiny images (likely icons), but be lenient with CDN images
            is_cdn = any(cdn in src.lower() for cdn in ['sanity', 'cloudinary', 'imgix',
                                                          'cloudfront', 'cdn', 'unsplash'])
            width = img.get('width', '')
            height = img.get('height', '')
            try:
                if not is_cdn:
                    if width and int(width) < 50:
                        continue
                    if height and int(height) < 50:
                        continue
            except ValueError:
                pass

            # Skip common non-content images
            if alt and any(skip in alt.lower() for skip in ['avatar', 'profile', 'photo of', 'logo']):
                continue

            images.append({
                'url': src,
                'alt': alt,
                'title': title or alt,
            })

        # ===== Check for background images in style attributes (common in SPAs) =====
        for elem in soup.find_all(style=re.compile(r'background(-image)?:\s*url', re.I)):
            style = elem.get('style', '')
            # Extract URL from background-image: url(...)
            match = re.search(r'url\(["\']?([^"\')\s]+)["\']?\)', style)
            if match:
                bg_url = match.group(1)
                if not bg_url.startswith(('http://', 'https://')):
                    bg_url = urljoin(base_url, bg_url)
                if bg_url not in seen_urls and not bg_url.startswith('data:'):
                    seen_urls.add(bg_url)
                    images.append({
                        'url': bg_url,
                        'alt': 'Background image',
                        'title': '',
                    })

    except Exception as e:
        print(f"      Warning: Image extraction error: {e}")

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


def calculate_reading_time(text: str, page_reading_time: int = None) -> int:
    """
    Calculate estimated reading time in minutes.

    Args:
        text: The article content
        page_reading_time: Reading time from the page itself (if available)

    Returns:
        Reading time in minutes
    """
    # If the page provides its own reading time, prefer that
    if page_reading_time and page_reading_time > 0:
        return page_reading_time

    # Clean content: only count actual words, not garbage characters
    # Use regex to find real words (2+ letters)
    words = re.findall(r'\b[a-zA-Z]{2,}\b', text)
    word_count = len(words)

    # Average reading speed: 200-250 words per minute
    # Using 225 as a middle ground
    minutes = max(1, round(word_count / 225))
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
        # Escape quotes and newlines in description
        description = description.replace('"', '\\"').replace('\n', ' ').replace('\r', '')
        # Truncate only if very long (500+ chars)
        if len(description) > 500:
            description = description[:497] + '...'
        lines.append(f'description: "{description}"')

    # Indicate if TOC is included
    if has_toc:
        lines.append('has_toc: true')

    # Content type and version
    lines.append('content_type: "web_article"')
    lines.append(f'converter_version: "{CONVERTER_VERSION}"')

    lines.append('---')

    return '\n'.join(lines)


def remove_marketing_content(content: str) -> str:
    """
    Remove marketing, promotional, and related content sections from article.
    These sections typically appear at the end of articles and add noise for RAG.
    """
    lines = content.split('\n')
    clean_lines = []
    skip_rest = False

    # Patterns that indicate start of marketing/promotional content
    marketing_patterns = [
        # Subscribe/newsletter patterns
        r'^#+\s*subscribe',
        r'^#+\s*sign\s*up',
        r'^#+\s*join\s*(our|the)',
        r'^#+\s*get\s*(our|the|updates)',
        r'^#+\s*stay\s*(updated|informed|ahead)',
        r'^#+\s*newsletter',
        # Related content patterns
        r'^#+\s*(related|more|other|similar)\s*(posts?|articles?|content|reading)',
        r'^#+\s*you\s*(might|may)\s*(also\s*)?(like|enjoy)',
        r'^#+\s*recommended',
        r'^#+\s*from\s+the\s+(library|blog|archive)',
        r'^#+\s*content\s+from',
        r'^#+\s*(also|more)\s+on',
        # CTA patterns
        r'^#+\s*share\s+this',
        r'^#+\s*follow\s+us',
        r'^#+\s*connect\s+with',
        r'^#+\s*about\s+the\s+author',  # Usually at the very end
        # Specific promotional text
        r'^\*?do you have.*share with our community',
        r'^\*?join our contributor',
        r'^\*?we want to hear from you',
        # Medium responses/comments section
        r'^#+?\s*responses?\s*\(\d+\)',  # "Responses (2)" or "## Responses (5)"
        r'^responses?\s*\(\d+\)',  # Plain "Responses (2)" without heading
        r'^\*?\*?responses?\*?\*?\s*\(\d+\)',  # Bold responses header
        # Medium promotional CTA patterns (paragraph text, not headers)
        r'subscribe\s+here\.?\s*$',  # "Subscribe here." at end of line
        r'(million|thousand)\s+people\s+have\s+(used|read)',  # "X million people have used/read"
        r'Work with the best (designers|developers)',  # Medium/Crew promotional text
        # Comment/response patterns (when they appear as text)
        r'^BRAVO!',  # Common comment exclamations
        r'^Well said',
        r'^Thank YOU',
        r'^I (very much )?(appreciate|agree|love)',
    ]

    # Compile patterns
    compiled_patterns = [re.compile(p, re.I) for p in marketing_patterns]

    for i, line in enumerate(lines):
        # Check if this line matches a marketing pattern
        if not skip_rest:
            for pattern in compiled_patterns:
                if pattern.search(line.strip()):
                    skip_rest = True
                    break

        if not skip_rest:
            clean_lines.append(line)

    # If we removed too much (>50%), something went wrong - keep original
    if len(clean_lines) < len(lines) * 0.5:
        return content

    return '\n'.join(clean_lines)


def clean_markdown_for_rag(content: str) -> str:
    """
    Clean and optimize markdown content for RAG systems.
    Includes garbage character removal for corrupted SPA content.
    """

    # ===== Step 0: Remove marketing/promotional content =====
    content = remove_marketing_content(content)

    # ===== Step 1: Remove garbage/non-printable characters =====
    # Keep only printable ASCII and common Unicode, plus whitespace
    cleaned_chars = []
    for char in content:
        code = ord(char)
        # Keep: newline, tab, carriage return, and printable characters
        if char in '\n\r\t':
            cleaned_chars.append(char)
        elif code >= 0x20 and code < 0x7F:  # Printable ASCII
            cleaned_chars.append(char)
        elif code >= 0xA0 and code < 0x10000:  # Common Unicode (Latin, symbols, etc.)
            # Filter out certain problematic Unicode ranges
            if not (0x2000 <= code <= 0x200F):  # Zero-width and format chars
                cleaned_chars.append(char)
        # Drop everything else (control chars, private use, etc.)
    content = ''.join(cleaned_chars)

    # ===== Step 2: Remove repeated garbage patterns =====
    # Pattern like "aaa" or "xxx" repeated more than 3 times
    content = re.sub(r'(.)\1{10,}', r'\1\1\1', content)

    # Remove lines that are mostly non-word characters (garbage lines)
    clean_lines = []
    for line in content.split('\n'):
        if line.strip():
            # Count word characters vs total
            word_chars = len(re.findall(r'[a-zA-Z0-9]', line))
            total_chars = len(line.strip())
            # Keep line if at least 30% are word characters, or if it's short (headers, bullets)
            if total_chars < 5 or (word_chars / total_chars) >= 0.3:
                clean_lines.append(line)
        else:
            clean_lines.append(line)  # Keep blank lines
    content = '\n'.join(clean_lines)

    # ===== Step 3: Standard markdown cleanup =====
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

    # ===== Step 4: Clean up HTML entities and finalize =====
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

    # ===== MEDIUM SPECIAL HANDLING =====
    # Medium gates content for non-logged-in users, use Selenium with auth
    medium_selenium_html = None

    if is_medium_url(url):
        print("\n[Medium Detected] Using Selenium for authenticated access...")

        if MEDIUM_SUPPORT_AVAILABLE:
            medium_selenium_html, selenium_error = fetch_medium_with_selenium(url)
            if medium_selenium_html:
                print(f"      ✓ Got authenticated content via Selenium")
            else:
                print(f"      Selenium fetch failed: {selenium_error}")
                print("      Falling back to regular fetch (may be truncated)...")
        else:
            print("      Medium support not available - install with: pip install selenium webdriver-manager undetected-chromedriver")
            print("      Falling back to regular fetch (may be truncated)...")

    # Step 1: Fetch the URL
    if medium_selenium_html:
        print("\n[1/6] Using content from Selenium...")
        html_content = medium_selenium_html
    else:
        print("\n[1/6] Fetching URL...")
        html_content, error = fetch_url(url)
        if error:
            return False, f"Failed to fetch URL: {error}", None

        print(f"      Fetched {len(html_content):,} bytes")

    # Check if we got actual content or just a JS shell (common with SPAs)
    # Try a quick extraction to see if there's content
    if TRAFILATURA_AVAILABLE:
        test_extract = extract(html_content)
        if not test_extract or len(test_extract) < 200:
            print("      Page appears to be JS-rendered SPA, trying trafilatura's fetcher...")
            try:
                from trafilatura import fetch_url as traf_fetch
                better_html = traf_fetch(url)
                if better_html:
                    # Check if trafilatura's fetch got more content
                    test_extract2 = extract(better_html)
                    if test_extract2 and len(test_extract2) > len(test_extract or ''):
                        print(f"      Trafilatura fetch got {len(better_html):,} bytes with better content")
                        html_content = better_html
                    else:
                        print(f"      Trafilatura fetch didn't improve content")
                else:
                    print("      Trafilatura fetch returned no content")
            except Exception as e:
                print(f"      Trafilatura fetch failed: {e}")
        else:
            print(f"      Initial fetch has content ({len(test_extract)} chars extracted)")

    # Step 2: Extract metadata from multiple sources
    print("\n[2/6] Extracting metadata...")

    # Extract metadata from multiple sources
    json_ld_meta = extract_json_ld_metadata(html_content)
    og_meta = extract_opengraph_metadata(html_content)
    spa_meta = extract_spa_metadata(html_content, url)  # SPA-specific extraction
    html_meta = extract_html_metadata(html_content, url)

    # Merge metadata (priority: JSON-LD > OpenGraph > SPA > HTML)
    metadata = merge_metadata(json_ld_meta, og_meta, spa_meta, html_meta)

    # Medium-specific: If author looks like a URL, prefer the display name from SPA extraction
    # Medium's article:author meta tag contains URLs like https://medium.com/@username
    if is_medium_url(url) and metadata.get('author'):
        author = metadata['author']
        # Check if author is a URL (contains medium.com or starts with http)
        if 'medium.com' in author or author.startswith('http'):
            # Prefer SPA-extracted author (display name like "Mikael Cho")
            if spa_meta.get('author') and 'medium.com' not in spa_meta['author']:
                metadata['author'] = spa_meta['author']
                print(f"      Note: Using display name '{spa_meta['author']}' instead of URL")
            else:
                # Fallback: Extract username from URL and format as title case
                # e.g., https://medium.com/@mikaelcho -> "Mikaelcho"
                url_match = re.search(r'@([a-zA-Z0-9_]+)', author)
                if url_match:
                    username = url_match.group(1)
                    # Format as title case (capitalize first letter)
                    formatted_name = username.title()
                    metadata['author'] = formatted_name
                    print(f"      Note: Using username '{formatted_name}' from URL (display name not found)")

    # Extract tags/topics (also check SPA metadata for tags)
    tags = extract_tags_and_topics(html_content)
    if not tags and 'tags' in spa_meta:
        tags = spa_meta['tags']

    print(f"      Title: {metadata.get('title', 'Not found')}")
    print(f"      Author: {metadata.get('author', 'Not found')}")
    print(f"      Date: {metadata.get('publication_date', 'Not found')}")
    print(f"      Source: {metadata.get('source_name', 'Not found')}")
    if metadata.get('reading_time_raw'):
        print(f"      Reading time (from page): {metadata['reading_time_raw']} min")
    if tags:
        print(f"      Tags: {', '.join(tags[:5])}{'...' if len(tags) > 5 else ''}")

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
        all_images = extract_images(html_content, url)

        # Filter to only images that are actually referenced in the article content
        # This avoids downloading nav images, author avatars, related post images, etc.
        article_images = []
        for img in all_images:
            img_url = img['url']
            # Check if this image URL (or a variant) appears in the content
            # Handle various URL formats and partial matches
            url_parts = urlparse(img_url)
            img_filename = url_parts.path.split('/')[-1] if url_parts.path else ''

            # Check for direct URL reference or filename reference in content
            if (img_url in content or
                (img_filename and img_filename in content) or
                img.get('priority')):  # Always include og:image as it's the main article image
                article_images.append(img)

        print(f"      Found {len(all_images)} images on page, {len(article_images)} in article")

        if article_images:
            image_dir = output_path / image_subdir
            image_dir.mkdir(exist_ok=True)

            # Create base name for images
            base_name = sanitize_filename(f"{metadata.get('author', 'Unknown')} - {metadata.get('title', 'Article')[:40]} - {metadata.get('source_name', 'Web')}")

            downloaded = 0
            for idx, img in enumerate(article_images, 1):
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

            print(f"      Downloaded {downloaded}/{len(article_images)} article images")
    else:
        print("\n[4/6] Skipping image download...")

    # Step 5: Clean and optimize content
    print("\n[5/6] Cleaning content for RAG...")
    content = clean_markdown_for_rag(content)

    # Calculate reading time (use page's value if available)
    page_rt = metadata.get('reading_time_raw')
    reading_time = calculate_reading_time(content, page_rt)
    if page_rt:
        print(f"      Reading time: {reading_time} minutes (from page)")
    else:
        print(f"      Estimated reading time: {reading_time} minutes")

    # Generate TOC from the cleaned markdown content (not from HTML)
    # This ensures the TOC only includes article headings, not marketing/footer content
    toc = extract_toc_from_markdown(content)
    if toc:
        print(f"      TOC: {len(toc)} sections")

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
