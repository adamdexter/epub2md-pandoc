#!/usr/bin/env python3
"""
Reddit Browser Fetcher
======================
Fetches Reddit post JSON through a real Chrome driven by ``nodriver`` (the modern
successor to undetected-chromedriver), to get past Reddit's "Please wait for
verification" bot-check.

Approach mirrors the technique proven in the sibling `campfinder` project's
booker: a real, visible Chrome with a *persistent profile* produces a genuine
fingerprint, so the Cloudflare/Reddit interstitial clears on its own — no captcha
solver, no proxies. Once the gate clears we do an in-page ``fetch()`` of the
``.json`` endpoint, inheriting the verified session, and hand the JSON back to the
existing parser in ``html_to_md_converter``.

Feature-flagged behind nodriver's availability so the core app runs without it.

Usage:
    from reddit_browser import fetch_reddit_json_via_browser, REDDIT_BROWSER_AVAILABLE

    if REDDIT_BROWSER_AVAILABLE:
        data, error = fetch_reddit_json_via_browser(url)
"""

import asyncio
import json
import os
from typing import Any, Optional

# nodriver is an optional dependency; import is guarded so the base app still runs.
REDDIT_BROWSER_AVAILABLE = False
_NODRIVER_IMPORT_ERROR: Optional[str] = None
uc = None
try:
    import nodriver as uc
    REDDIT_BROWSER_AVAILABLE = True
except Exception as e:  # ImportError, or environment/runtime issues
    _NODRIVER_IMPORT_ERROR = str(e)

# A dedicated, persistent Chrome profile. Reusing it across runs is the key lever:
# a warmed-up real profile clears Reddit's verification faster on later runs.
REDDIT_PROFILE_DIR = os.path.join(os.path.dirname(__file__), '.reddit_chrome_profile')

# How long (seconds) to wait for the verification interstitial to clear.
REDDIT_GATE_TIMEOUT = 40

# Title/body markers that indicate we're still on a bot-check interstitial.
REDDIT_GATE_MARKERS = (
    'please wait',
    'just a moment',
    'verifying you are',
    'verify you are human',
    'checking your browser',
)

# Runs inside the verified page: fetch the post's .json in-session, return text.
# Using location.* means /s/ share-link redirects are already resolved for us.
_FETCH_JSON_JS = """
(async () => {
    let p = location.pathname.replace(/\\/$/, '');
    if (!p.endsWith('.json')) p += '.json';
    const sep = location.search ? location.search + '&' : '?';
    const u = location.origin + p + sep + 'raw_json=1';
    const r = await fetch(u, { headers: { 'Accept': 'application/json' }, credentials: 'include' });
    return await r.text();
})()
"""


async def _fetch_async(url: str, headless: bool) -> tuple[Optional[str], bool]:
    """Drive the browser: pass the gate on the post page, then in-page fetch JSON."""
    browser = await uc.start(
        user_data_dir=REDDIT_PROFILE_DIR,
        headless=headless,
        browser_args=['--no-first-run', '--no-default-browser-check'],
    )
    try:
        tab = await browser.get(url)

        # Wait for the Cloudflare/Reddit verification interstitial to clear.
        cleared = False
        for _ in range(REDDIT_GATE_TIMEOUT):
            await tab.sleep(1)
            try:
                title = (await tab.evaluate('document.title') or '').lower()
            except Exception:
                title = ''
            if title and not any(m in title for m in REDDIT_GATE_MARKERS):
                cleared = True
                break

        # Attempt the in-page fetch regardless — the gate may have cleared without
        # a title change, and the fetch result is the real signal of success.
        text = await tab.evaluate(_FETCH_JSON_JS, await_promise=True)
        return text, cleared
    finally:
        try:
            browser.stop()
        except Exception:
            pass


def fetch_reddit_json_via_browser(url: str, headless: bool = False) -> tuple[Optional[Any], Optional[str]]:
    """
    Fetch a Reddit post's JSON via a real browser.

    Args:
        url: The Reddit post (or /s/ share) URL.
        headless: Run Chrome without a visible window. Defaults to False because a
            visible, real browser clears the verification gate most reliably.

    Returns:
        Tuple of (parsed_json, error_message).
    """
    if not REDDIT_BROWSER_AVAILABLE:
        hint = f" ({_NODRIVER_IMPORT_ERROR})" if _NODRIVER_IMPORT_ERROR else ""
        return None, f"nodriver not installed — run: pip install nodriver{hint}"

    print("      Launching a real browser (nodriver) to pass Reddit's verification...", flush=True)

    # Run in a fresh event loop so this works inside the GUI's worker thread.
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        text, cleared = loop.run_until_complete(_fetch_async(url, headless))
    except Exception as e:
        return None, f"browser fetch failed: {e}"
    finally:
        try:
            loop.close()
        finally:
            asyncio.set_event_loop(None)

    if not text or not text.strip():
        return None, "browser returned no content (verification may not have cleared)"
    text = text.strip()
    if text.startswith('<'):
        return None, "Reddit still returned HTML (verification did not clear — try re-running)"
    try:
        return json.loads(text), None
    except ValueError:
        return None, "browser fetch did not return valid JSON"


# ============================================================================
# CLI ENTRY POINT (for testing the browser fetch in isolation)
# ============================================================================

if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python reddit_browser.py <reddit_post_url>")
        sys.exit(1)

    if not REDDIT_BROWSER_AVAILABLE:
        print(f"nodriver not available: {_NODRIVER_IMPORT_ERROR}")
        print("Install with: pip install nodriver")
        sys.exit(1)

    data, error = fetch_reddit_json_via_browser(sys.argv[1])
    if error:
        print(f"Error: {error}")
        sys.exit(1)
    print(f"Got JSON ({len(json.dumps(data)):,} chars)")
