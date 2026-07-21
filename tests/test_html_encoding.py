"""Tests for content-encoding handling in the web-article fetcher.

Regression for the bug where brotli-compressed responses (Content-Encoding: br)
were handed back as raw bytes and mis-decoded into garbage, causing all
extractors to fail on affected sites.
"""

import gzip

from html_to_md_converter import (
    DEFAULT_HEADERS,
    _manual_decompress,
    _supported_accept_encoding,
)


def test_accept_encoding_only_advertises_decodable():
    """We must never advertise a compression we can't decode."""
    advertised = {e.strip() for e in DEFAULT_HEADERS['Accept-Encoding'].split(',')}
    # gzip/deflate are always decodable (stdlib zlib).
    assert {'gzip', 'deflate'} <= advertised
    # br/zstd only when their optional decoders are importable.
    import importlib.util
    if not (importlib.util.find_spec('brotli') or importlib.util.find_spec('brotlicffi')):
        assert 'br' not in advertised
    if not importlib.util.find_spec('zstandard'):
        assert 'zstd' not in advertised


def test_supported_accept_encoding_is_comma_list():
    value = _supported_accept_encoding()
    assert value.startswith('gzip, deflate')


def test_manual_decompress_unknown_encoding_returns_none():
    assert _manual_decompress(b'whatever', 'gzip') is None
    assert _manual_decompress(b'whatever', 'identity') is None


def test_manual_decompress_brotli_roundtrip_if_available():
    import importlib.util

    if not importlib.util.find_spec('brotli'):
        return  # decoder not installed in this environment; nothing to assert
    import brotli

    original = b'<html><body>hello brotli</body></html>'
    assert _manual_decompress(brotli.compress(original), 'br') == original


def test_manual_decompress_handles_corrupt_data():
    # Garbage in -> None (never raises).
    assert _manual_decompress(gzip.compress(b'x'), 'br') is None


class _FakeResponse:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, text):
        self.status_code = 200
        self.headers = {}
        self.text = text
        self.content = text.encode('utf-8')
        self.encoding = 'utf-8'
        self.apparent_encoding = 'utf-8'

    def raise_for_status(self):
        pass


def test_fetch_url_retries_without_verification_on_ssl_error(monkeypatch):
    """A site with a broken cert chain should still convert via a no-verify retry."""
    import requests

    import html_to_md_converter as h

    calls = []

    def fake_get(self, url, **kwargs):
        calls.append(kwargs.get('verify', True))
        # First call (verify defaulted True) fails; retry with verify=False succeeds.
        if kwargs.get('verify', True) is not False:
            raise requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
        return _FakeResponse("<html><body>recovered content</body></html>")

    monkeypatch.setattr(requests.Session, "get", fake_get)
    content, error = h.fetch_url("https://broken-chain.example/article")

    assert error is None
    assert "recovered content" in content
    # Verified attempt first, then an explicit verify=False retry.
    assert calls == [True, False]


def test_fetch_url_ssl_error_message_is_not_connectivity(monkeypatch):
    """If even the no-verify retry fails, the error must name SSL, not the network."""
    import requests

    import html_to_md_converter as h

    def always_ssl(self, url, **kwargs):
        raise requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")

    monkeypatch.setattr(requests.Session, "get", always_ssl)
    content, error = h.fetch_url("https://broken-chain.example/article")

    assert content is None
    assert "SSL" in error and "internet connection" not in error
