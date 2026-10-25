import unittest

from crawler.urls import (
    UnsafeUrlError,
    ensure_safe_url,
    is_same_site,
    normalize_url,
    site_key,
)


class NormalizeUrlTests(unittest.TestCase):
    def test_adds_https_and_removes_fragment(self):
        self.assertEqual(
            normalize_url("example.com/path#section"),
            "https://example.com/path",
        )

    def test_removes_default_port(self):
        self.assertEqual(
            normalize_url("http://Example.COM:80/a"),
            "http://example.com/a",
        )

    def test_resolves_relative_url(self):
        self.assertEqual(
            normalize_url("../b?q=1", base="https://example.com/a/c"),
            "https://example.com/b?q=1",
        )

    def test_rejects_non_http_scheme(self):
        with self.assertRaises(UnsafeUrlError):
            normalize_url("ftp://example.com/file")

    def test_rejects_credentials(self):
        with self.assertRaises(UnsafeUrlError):
            normalize_url("https://user:pass@example.com/")

    def test_rejects_empty_host(self):
        with self.assertRaises(UnsafeUrlError):
            normalize_url("https:///path")

    def test_rejects_malformed_ipv6(self):
        with self.assertRaises(UnsafeUrlError):
            normalize_url("http://[::1")

    def test_rejects_whitespace_in_host(self):
        with self.assertRaises(UnsafeUrlError):
            normalize_url("https://exa mple.com")

    def test_protocol_relative_url(self):
        self.assertEqual(
            normalize_url("//example.com/a"), "https://example.com/a"
        )

    def test_supports_ipv6(self):
        self.assertEqual(normalize_url("http://[::1]:8080/a"), "http://[::1]:8080/a")

    def test_site_key_ignores_www(self):
        self.assertEqual(site_key("https://www.Example.com/a"), "example.com")
        self.assertTrue(
            is_same_site("https://example.com/a", "http://www.example.com/b")
        )
        self.assertFalse(is_same_site("https://a.com", "https://b.com"))


class SafetyTests(unittest.TestCase):
    def test_private_address_rejected_by_default(self):
        with self.assertRaises(UnsafeUrlError):
            ensure_safe_url("http://127.0.0.1/", allow_private=False)

    def test_private_address_allowed_when_opted_in(self):
        addresses = ensure_safe_url("http://127.0.0.1/", allow_private=True)
        self.assertIn("127.0.0.1", addresses)

    def test_cloud_metadata_always_rejected(self):
        with self.assertRaises(UnsafeUrlError):
            ensure_safe_url("http://169.254.169.254/latest/meta-data", True)


if __name__ == "__main__":
    unittest.main()
