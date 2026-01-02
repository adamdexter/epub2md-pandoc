# Architecture Documentation

This document provides internal documentation for developers working on the EPUB & Web to Markdown Converter.

## Project Structure

```
epub2md-pandoc/
├── epub_to_md_converter.py    # Core EPUB to Markdown conversion
├── html_to_md_converter.py    # Web article to Markdown conversion
├── medium_scraper.py          # Medium-specific authentication & scraping
├── gui.py                     # Flask web GUI
├── templates/
│   └── index.html             # GUI frontend
├── install.sh                 # Linux/macOS installer
├── install.bat                # Windows installer
├── run_gui.sh                 # Linux/macOS GUI launcher
├── run_gui.bat                # Windows GUI launcher
├── requirements.txt           # Python dependencies
└── .medium_cookies/           # Medium session cookies (gitignored)
└── .medium_chrome_profile/    # Chrome profile for Medium (gitignored)
```

## Module Architecture

### Core Modules

#### `epub_to_md_converter.py`
- **Purpose**: Batch convert EPUB files to AI-optimized Markdown
- **Key Features**: Metadata extraction, Pandoc integration, artifact cleanup
- **Dependencies**: Pandoc (external), Python standard library

#### `html_to_md_converter.py` (v1.0.17+)
- **Purpose**: Convert web articles to AI-optimized Markdown
- **Key Features**: URL fetching, content extraction, image downloading
- **Dependencies**: requests, trafilatura, beautifulsoup4, readability-lxml

#### `medium_scraper.py` (v2.5.15+)
- **Purpose**: Handle Medium article authentication and scraping
- **Why Separate Module**:
  - Feature flagging: Can be disabled without affecting core functionality
  - Product segmentation: Different licensing/pricing for Medium support
  - Clean architecture: Selenium dependencies isolated
  - Future extensibility: Template for LinkedIn, Substack, etc.
- **Dependencies**: selenium, webdriver-manager, undetected-chromedriver

### Module Dependency Graph

```
gui.py
  ├── epub_to_md_converter.py
  └── html_to_md_converter.py
        └── medium_scraper.py (optional)
```

## Medium Scraper Architecture

### Why a Separate Module?

Medium articles are gated behind authentication and Cloudflare protection. This requires:
1. Browser automation (Selenium)
2. Anti-detection (undetected-chromedriver)
3. Session persistence (cookies)
4. Manual login flow

These are heavy dependencies that shouldn't affect users who only need basic web article conversion.

### Feature Flag Pattern

```python
# In html_to_md_converter.py
MEDIUM_SUPPORT_AVAILABLE = False

try:
    from medium_scraper import (
        is_medium_url,
        fetch_medium_with_selenium,
        MEDIUM_SUPPORT_AVAILABLE,
        SELENIUM_AVAILABLE
    )
except ImportError:
    # Stub functions - core functionality works without Medium support
    def is_medium_url(url: str) -> bool:
        return False
```

### Python 3.12+ Compatibility

Python 3.12 removed `distutils`. The `medium_scraper.py` includes a compatibility shim:

```python
import sys
if 'distutils' not in sys.modules:
    try:
        from setuptools import _distutils_hack
        _distutils_hack.add_shim()
    except (ImportError, AttributeError):
        try:
            import setuptools._distutils as _distutils
            sys.modules['distutils'] = _distutils
            sys.modules['distutils.version'] = _distutils.version
        except (ImportError, AttributeError):
            pass
```

This MUST run before importing `undetected-chromedriver`.

### Cookie & Session Management

Medium sessions are persisted in two ways:
1. **Cookies**: Saved to `.medium_cookies/medium_cookies.pkl`
2. **Chrome Profile**: Stored in `.medium_chrome_profile/`

Both directories are created automatically and should be gitignored.

### Cloudflare Bypass

The module uses `undetected-chromedriver` which:
- Patches Chrome to avoid automation detection
- Bypasses Cloudflare's browser verification
- Uses a dedicated profile (can run alongside your regular Chrome)

Falls back to regular Selenium if undetected-chromedriver isn't available.

## Adding New Platform Support

To add support for a new platform (e.g., LinkedIn, Substack):

1. **Create a new module** (e.g., `linkedin_scraper.py`)
2. **Follow the pattern** from `medium_scraper.py`:
   - Export `is_<platform>_url()` function
   - Export `fetch_<platform>_with_selenium()` function
   - Export `<PLATFORM>_SUPPORT_AVAILABLE` flag
3. **Update `html_to_md_converter.py`**:
   - Add import with try/except
   - Add platform detection in `convert_url_to_markdown()`
4. **Update installers** if new dependencies are needed

### Example Template

```python
# linkedin_scraper.py
LINKEDIN_SUPPORT_AVAILABLE = False

try:
    from selenium import webdriver
    # ... setup code ...
    LINKEDIN_SUPPORT_AVAILABLE = True
except ImportError:
    pass

def is_linkedin_url(url: str) -> bool:
    """Check if URL is a LinkedIn article."""
    parsed = urlparse(url)
    return 'linkedin.com' in parsed.netloc

def fetch_linkedin_with_selenium(url: str) -> Tuple[Optional[str], Optional[str]]:
    """Fetch LinkedIn article with authentication."""
    # ... implementation ...
```

## Version Numbering

- **GUI Version** (index.html): `2.x.y` - Major.Minor.Patch
- **Converter Version** (html_to_md_converter.py): `1.x.y` - tracks converter logic changes

Update both when making significant changes.

## Testing

### Syntax Check
```bash
python3 -m py_compile medium_scraper.py
python3 -m py_compile html_to_md_converter.py
```

### Import Chain Test
```bash
python3 -c "from html_to_md_converter import is_medium_url, MEDIUM_SUPPORT_AVAILABLE; print(f'Medium: {MEDIUM_SUPPORT_AVAILABLE}')"
```

### Medium URL Detection
```bash
python3 -c "
from medium_scraper import is_medium_url
print(is_medium_url('https://medium.com/@user/article'))  # True
print(is_medium_url('https://user.medium.com/article'))   # True
print(is_medium_url('https://example.com/article'))       # False
"
```

## Security Considerations

1. **Credentials**: Never log credentials. Cookie files contain session tokens.
2. **Chrome Profile**: Contains browsing data - don't commit to git.
3. **Selenium**: Runs with `--no-sandbox` for compatibility - be aware of implications.

## Troubleshooting

### "distutils not found"
Ensure `setuptools` is installed and the shim runs before importing undetected-chromedriver.

### "user data directory already in use"
Another Selenium session is running. Close Chrome instances using the Medium profile.

### Cloudflare loop
- Try clearing `.medium_chrome_profile/` directory
- Ensure undetected-chromedriver is up to date
- May need to manually solve CAPTCHA once
