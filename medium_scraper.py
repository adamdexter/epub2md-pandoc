#!/usr/bin/env python3
"""
Medium Article Scraper
======================
Handles authenticated access to Medium articles using Selenium.

Medium gates content for non-logged-in users. This module provides:
1. Cookie-based session persistence
2. Manual login flow for authentication
3. Cloudflare bypass using undetected-chromedriver

Usage:
    from medium_scraper import is_medium_url, fetch_medium_with_selenium, MEDIUM_SUPPORT_AVAILABLE

    if MEDIUM_SUPPORT_AVAILABLE and is_medium_url(url):
        html_content, error = fetch_medium_with_selenium(url)
"""

import os
import time
import pickle
import stat
from typing import Optional, Tuple
from urllib.parse import urlparse

# ============================================================================
# SELENIUM SETUP WITH CLOUDFLARE BYPASS
# ============================================================================

# Python 3.12+ removed distutils - set up compatibility shim before importing undetected-chromedriver
import sys
if 'distutils' not in sys.modules:
    try:
        # Try to use setuptools' bundled distutils
        from setuptools import _distutils_hack
        _distutils_hack.add_shim()
    except (ImportError, AttributeError):
        try:
            # Alternative: manually add the shim
            import setuptools._distutils as _distutils
            sys.modules['distutils'] = _distutils
            sys.modules['distutils.version'] = _distutils.version
        except (ImportError, AttributeError):
            pass

# Try undetected-chromedriver first (best for bypassing Cloudflare)
UNDETECTED_CHROME_AVAILABLE = False
UNDETECTED_CHROME_ERROR = None
uc = None
try:
    import undetected_chromedriver as uc
    UNDETECTED_CHROME_AVAILABLE = True
except ImportError as e:
    UNDETECTED_CHROME_ERROR = f"ImportError: {e}"
except Exception as e:
    UNDETECTED_CHROME_ERROR = f"Error: {e}"

# Fall back to regular Selenium
SELENIUM_AVAILABLE = False
webdriver = None
By = None
WebDriverWait = None
EC = None
Options = None
Service = None
TimeoutException = None
NoSuchElementException = None
ChromeDriverManager = None

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    pass

# Flag to indicate if Medium support is available
MEDIUM_SUPPORT_AVAILABLE = SELENIUM_AVAILABLE or UNDETECTED_CHROME_AVAILABLE


# ============================================================================
# MEDIUM CONFIGURATION
# ============================================================================
MEDIUM_COOKIES_DIR = os.path.join(os.path.dirname(__file__), '.medium_cookies')
MEDIUM_PROFILE_DIR = os.path.join(os.path.dirname(__file__), '.medium_chrome_profile')
MEDIUM_MANUAL_LOGIN_TIMEOUT = 180  # seconds to wait for manual login
MEDIUM_BROWSER_TIMEOUT = 30  # seconds for page loads
MEDIUM_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


# ============================================================================
# MEDIUM URL DETECTION
# ============================================================================

def is_medium_url(url: str) -> bool:
    """
    Check if a URL is a Medium article.

    Handles:
    - medium.com/@username/article
    - medium.com/publication/article
    - username.medium.com/article
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    # Direct medium.com URLs
    if host in ('medium.com', 'www.medium.com'):
        return True

    # Subdomain pattern: username.medium.com
    if host.endswith('.medium.com'):
        return True

    return False


# ============================================================================
# COOKIE MANAGEMENT
# ============================================================================

def get_medium_cookie_path() -> str:
    """Get the path for Medium cookies file."""
    os.makedirs(MEDIUM_COOKIES_DIR, exist_ok=True)
    return os.path.join(MEDIUM_COOKIES_DIR, 'medium_cookies.pkl')


def save_medium_cookies(driver) -> bool:
    """Save Medium session cookies to file."""
    try:
        cookie_path = get_medium_cookie_path()
        cookies = driver.get_cookies()
        with open(cookie_path, 'wb') as f:
            pickle.dump(cookies, f)
        print(f"      Saved {len(cookies)} cookies for future sessions")
        return True
    except Exception as e:
        print(f"      Warning: Could not save cookies: {e}")
        return False


def load_medium_cookies(driver) -> bool:
    """Load Medium session cookies from file."""
    try:
        cookie_path = get_medium_cookie_path()
        if not os.path.exists(cookie_path):
            return False

        # First navigate to Medium domain so cookies can be set
        driver.get("https://medium.com")
        time.sleep(2)

        with open(cookie_path, 'rb') as f:
            cookies = pickle.load(f)

        for cookie in cookies:
            try:
                # Remove expiry if it's causing issues
                if 'expiry' in cookie:
                    del cookie['expiry']
                driver.add_cookie(cookie)
            except Exception:
                pass  # Some cookies may fail, that's OK

        print(f"      Loaded {len(cookies)} saved cookies")
        return True
    except Exception as e:
        print(f"      Could not load cookies: {e}")
        return False


# ============================================================================
# WEBDRIVER SETUP
# ============================================================================

def setup_medium_driver(headless: bool = False):
    """
    Set up Chrome WebDriver for Medium scraping.

    Uses undetected-chromedriver if available (best for bypassing Cloudflare),
    otherwise falls back to regular Selenium with anti-detection options.

    Args:
        headless: Run browser in headless mode (set False for manual login)

    Returns:
        WebDriver instance or None if setup fails
    """
    # Try undetected-chromedriver first (best for Cloudflare bypass)
    if UNDETECTED_CHROME_AVAILABLE:
        try:
            print("      Using undetected-chromedriver (Cloudflare bypass mode)")
            print(f"      [DEBUG] undetected-chromedriver version: {getattr(uc, '__version__', 'unknown')}")

            # Create dedicated profile directory for persistence
            os.makedirs(MEDIUM_PROFILE_DIR, exist_ok=True)

            options = uc.ChromeOptions()

            # Use dedicated profile (not your main Chrome - can run alongside)
            options.add_argument(f'--user-data-dir={MEDIUM_PROFILE_DIR}')

            if headless:
                options.add_argument('--headless=new')
                print("      Running in headless mode")
            else:
                print("      Running in visible mode (browser window will open)")

            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--window-size=1920,1080')

            # undetected-chromedriver handles most anti-detection automatically
            driver = uc.Chrome(options=options, use_subprocess=True)

            if not headless:
                driver.maximize_window()

            return driver

        except Exception as e:
            print(f"      undetected-chromedriver failed: {e}")
            print("      Falling back to regular Selenium...")

    # Show why undetected-chromedriver isn't being used
    if not UNDETECTED_CHROME_AVAILABLE:
        print(f"      [DEBUG] undetected-chromedriver not available: {UNDETECTED_CHROME_ERROR}")
        print("      Falling back to regular Selenium (may be detected by Cloudflare)")

    # Fall back to regular Selenium
    if not SELENIUM_AVAILABLE:
        print("      Error: Selenium not installed. Run: pip install selenium webdriver-manager")
        print("      For best results with Medium, also install: pip install undetected-chromedriver")
        return None

    try:
        chrome_options = Options()

        # Create dedicated profile directory for persistence
        os.makedirs(MEDIUM_PROFILE_DIR, exist_ok=True)
        chrome_options.add_argument(f'--user-data-dir={MEDIUM_PROFILE_DIR}')
        print(f"      Using dedicated profile: {MEDIUM_PROFILE_DIR}")

        if headless:
            chrome_options.add_argument('--headless=new')
            print("      Running in headless mode")
        else:
            print("      Running in visible mode (browser window will open)")

        # Comprehensive anti-detection options
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('--disable-infobars')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-popup-blocking')
        chrome_options.add_argument('--start-maximized')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument(f'user-agent={MEDIUM_USER_AGENT}')

        # Disable automation flags
        chrome_options.add_experimental_option('excludeSwitches', ['enable-automation', 'enable-logging'])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        chrome_options.add_experimental_option('prefs', {
            'credentials_enable_service': False,
            'profile.password_manager_enabled': False,
            'profile.default_content_setting_values.notifications': 2,
        })

        # Initialize driver
        driver_path = ChromeDriverManager().install()

        # Fix for webdriver-manager bug: sometimes returns wrong file
        if not os.access(driver_path, os.X_OK) or 'THIRD_PARTY' in driver_path:
            driver_dir = os.path.dirname(driver_path)
            for file in os.listdir(driver_dir):
                if file == 'chromedriver' or file == 'chromedriver.exe':
                    potential_path = os.path.join(driver_dir, file)
                    if os.path.isfile(potential_path):
                        if not os.access(potential_path, os.X_OK):
                            os.chmod(potential_path, os.stat(potential_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                        driver_path = potential_path
                        break

        service = Service(driver_path)
        driver = webdriver.Chrome(service=service, options=chrome_options)

        # Remove webdriver property to avoid detection
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
            '''
        })

        if not headless:
            driver.maximize_window()

        return driver

    except Exception as e:
        error_msg = str(e)
        if 'user data directory is already in use' in error_msg.lower():
            print("      Error: Another Selenium session is running. Please wait or restart.")
        else:
            print(f"      Error setting up Chrome WebDriver: {e}")
        return None


# ============================================================================
# LOGIN HANDLING
# ============================================================================

def check_medium_login_status_on_current_page(driver) -> bool:
    """Check if we're logged into Medium based on current page (no navigation)."""
    try:
        if driver is None:
            return False

        # Check the current page source without navigating
        page_source = driver.page_source
        if not page_source:
            return False

        page_source_lower = page_source.lower()

        # Logged-in indicators (more reliable)
        logged_in_indicators = [
            'write a story',
            'new story',
            '"isAuthenticated":true',
            'data-testid="headerUserButton"',
        ]

        # Check for logged-in indicators
        if any(ind in page_source_lower or ind in page_source for ind in logged_in_indicators):
            return True

        # Try to find user button/avatar
        try:
            driver.find_element(By,
                "button[data-testid='headerUserButton'], "
                "[data-testid='userButton'], "
                "img[alt*='profile' i], "
                ".avatar"
            )
            return True
        except:
            pass

        return False

    except Exception as e:
        # Window might be closed - that's OK
        return False


def medium_manual_login(driver) -> bool:
    """
    Prompt user to manually log in to Medium.

    Opens the login page and waits for user to complete login.
    """
    try:
        print("\n      ╔════════════════════════════════════════════════════════╗")
        print("      ║  MEDIUM LOGIN REQUIRED                                  ║")
        print("      ║  Please log in to Medium in the browser window.         ║")
        print("      ║  You have 3 minutes to complete login.                  ║")
        print("      ║  The browser will NOT close automatically.              ║")
        print("      ╚════════════════════════════════════════════════════════╝\n")

        # Navigate to Medium login
        driver.get("https://medium.com/m/signin")
        time.sleep(2)

        # Wait for user to log in
        start_time = time.time()
        timeout = MEDIUM_MANUAL_LOGIN_TIMEOUT
        last_url = ""

        while time.time() - start_time < timeout:
            try:
                current_url = driver.current_url or ""
            except Exception as e:
                # Window might have changed/closed - try to recover
                print(f"      [DEBUG] URL check failed: {e}")
                time.sleep(2)
                continue

            # Check if URL changed from login pages
            if current_url != last_url:
                last_url = current_url
                print(f"      [DEBUG] URL: {current_url[:60]}...")

            # Check if user has navigated away from login pages
            if current_url and '/signin' not in current_url and '/login' not in current_url and '/callback' not in current_url:
                # Give the page a moment to load
                time.sleep(3)

                # Check current page for login indicators (don't navigate away!)
                if check_medium_login_status_on_current_page(driver):
                    print("      ✓ Login successful!")
                    save_medium_cookies(driver)
                    return True

                # Even if we can't confirm login indicators, if we're on medium.com
                # and not on signin, we're probably logged in
                if 'medium.com' in current_url:
                    print("      ✓ Login appears successful (navigated away from signin)")
                    save_medium_cookies(driver)
                    return True

            time.sleep(2)
            remaining = int(timeout - (time.time() - start_time))
            if remaining % 30 == 0 and remaining > 0:
                print(f"      Waiting for login... ({remaining}s remaining)")

        print("      ✗ Login timed out")
        return False

    except Exception as e:
        print(f"      Error during manual login: {e}")
        return False


# ============================================================================
# MAIN FETCH FUNCTION
# ============================================================================

def fetch_medium_with_selenium(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch Medium article using Selenium with authentication.

    Strategy:
    1. Use dedicated profile (persists login across sessions)
    2. Try fetching article directly (may already be logged in)
    3. If not logged in, prompt for manual login
    4. Uses undetected-chromedriver if available to bypass Cloudflare

    Returns:
        Tuple of (html_content, error_message)
    """
    if not SELENIUM_AVAILABLE and not UNDETECTED_CHROME_AVAILABLE:
        return None, "Selenium not available. Install with: pip install selenium webdriver-manager undetected-chromedriver"

    driver = None
    try:
        # Open browser with dedicated profile (persists across sessions)
        print("      Opening browser...")
        driver = setup_medium_driver(headless=False)
        if not driver:
            return None, "Failed to set up browser"

        # Go directly to the article first
        print(f"      Navigating to article...")
        driver.get(url)
        time.sleep(4)

        # Check if we got full content
        page_source = driver.page_source
        if page_source and len(page_source) > 10000:
            page_lower = page_source.lower()
            # Check for paywall indicators
            if 'member-only story' not in page_lower and 'upgrade to read' not in page_lower:
                print("      ✓ Successfully fetched article")

                # Scroll to trigger lazy loading
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(1)

                page_source = driver.page_source
                print(f"      ✓ Fetched {len(page_source):,} bytes")
                save_medium_cookies(driver)
                return page_source, None
            else:
                print("      Article is member-only, need login...")

        # Need to login
        print("")
        print("      ╔════════════════════════════════════════════════════════╗")
        print("      ║  MEDIUM LOGIN REQUIRED                                  ║")
        print("      ║  Please log in to Medium in the browser window.         ║")
        print(f"      ║  You have {MEDIUM_MANUAL_LOGIN_TIMEOUT // 60} minutes to complete login.                  ║")
        print("      ║  Your session will be saved for future use.             ║")
        print("      ╚════════════════════════════════════════════════════════╝")
        print("")

        if medium_manual_login(driver):
            print(f"      Fetching article...")
            driver.get(url)
            time.sleep(5)

            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)

            page_source = driver.page_source
            if page_source and len(page_source) > 5000:
                print(f"      ✓ Fetched {len(page_source):,} bytes")
                return page_source, None
            else:
                return None, "Failed to fetch article content after login"
        else:
            return None, "Login failed or timed out"

    except Exception as e:
        error_msg = str(e)
        if 'user data directory is already in use' in error_msg.lower():
            return None, "Another converter session is running. Please wait or close it."
        return None, f"Selenium error: {error_msg}"

    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass


# ============================================================================
# CLI ENTRY POINT (for testing)
# ============================================================================

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python medium_scraper.py <medium_url>")
        print("\nThis module handles authenticated Medium article fetching.")
        print("For full conversion to Markdown, use html_to_md_converter.py instead.")
        sys.exit(1)

    url = sys.argv[1]
    if not is_medium_url(url):
        print(f"Warning: URL does not appear to be a Medium article: {url}")

    print(f"Fetching Medium article: {url}")
    html_content, error = fetch_medium_with_selenium(url)

    if error:
        print(f"Error: {error}")
        sys.exit(1)

    print(f"\nSuccessfully fetched {len(html_content):,} bytes")
    print("HTML content preview (first 500 chars):")
    print("-" * 40)
    print(html_content[:500])
