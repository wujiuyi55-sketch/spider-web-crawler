import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from crawler.config import CrawlConfig
from crawler.engine import Crawler


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests_seen: list[str] = []

    def log_message(self, *_args):
        return

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        type(self).requests_seen.append(path)
        port = self.server.server_address[1]
        if path == "/robots.txt":
            self._send(
                200,
                b"User-agent: *\nDisallow: /private\n",
                "text/plain; charset=utf-8",
            )
        elif path == "/":
            html = f"""
            <html lang="zh-CN"><head><title>首页</title></head><body>
            <h1>首页</h1>
            <a href="/about">关于</a>
            <a href="/private">私密</a>
            <a href="/nofollow" rel="nofollow">不跟随</a>
            <a href="/data.json">数据</a>
            <a href="http://localhost:{port}/external">站外</a>
            </body></html>
            """
            self._send(200, html.encode(), "text/html; charset=utf-8")
        elif path == "/about":
            self._send(
                200,
                b'<html><head><title>\xe5\x85\xb3\xe4\xba\x8e</title></head>'
                b'<body><h1>\xe5\x85\xb3\xe4\xba\x8e</h1>'
                b'<a href="/deep">\xe6\xb7\xb1\xe5\xb1\x82</a></body></html>',
                "text/html; charset=utf-8",
            )
        elif path == "/deep":
            self._send(
                200,
                b"<html><head><title>Deep</title></head><body>deep</body></html>",
                "text/html; charset=utf-8",
            )
        elif path == "/private":
            self._send(
                200,
                b"<html><head><title>Private</title></head><body>secret</body></html>",
                "text/html; charset=utf-8",
            )
        elif path == "/nofollow":
            self._send(
                200,
                b"<html><head><title>Nofollow</title></head><body>nofollow</body></html>",
                "text/html; charset=utf-8",
            )
        elif path == "/data.json":
            self._send(200, b'{"ok": true}', "application/json")
        elif path == "/external":
            self._send(
                200,
                b"<html><head><title>External</title></head><body>external</body></html>",
                "text/html; charset=utf-8",
            )
        else:
            self._send(404, b"not found", "text/plain")


class CrawlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}/"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        FixtureHandler.requests_seen = []

    def _crawl(self, **overrides):
        defaults = dict(
            depth=2,
            max_pages=10,
            delay=0,
            timeout=5,
            respect_robots=True,
            same_site=True,
            allow_private=True,
        )
        defaults.update(overrides)
        config = CrawlConfig(**defaults)
        return Crawler(config).crawl(self.base)

    def test_depth_one_only_fetches_start_page(self):
        result = self._crawl(depth=1)
        self.assertEqual(result["successes"], 1)
        self.assertEqual(len(result["pages"]), 1)
        self.assertEqual(result["pages"][0]["title"], "首页")
        self.assertNotIn("/about", FixtureHandler.requests_seen)

    def test_depth_two_follows_same_site_and_respects_robots(self):
        result = self._crawl()
        urls = [page["url"] for page in result["pages"]]
        self.assertIn(self.base, urls)
        self.assertIn(self.base + "about", urls)
        self.assertNotIn(self.base + "private", urls)
        self.assertNotIn(self.base + "nofollow", urls)
        self.assertNotIn("/private", FixtureHandler.requests_seen)
        self.assertNotIn("/nofollow", FixtureHandler.requests_seen)
        self.assertGreaterEqual(result["skipped_count"], 1)

    def test_respect_robots_can_be_disabled(self):
        result = self._crawl(respect_robots=False)
        urls = [page["url"] for page in result["pages"]]
        self.assertIn(self.base + "private", urls)

    def test_max_pages_is_enforced(self):
        result = self._crawl(depth=3, max_pages=2)
        self.assertEqual(result["successes"] + result["error_count"], 2)
        self.assertLessEqual(len(result["pages"]), 2)
        self.assertTrue(result["limit_reached"])

    def test_same_site_scope_excludes_other_host(self):
        result = self._crawl()
        urls = [page["url"] for page in result["pages"]]
        self.assertFalse(any("localhost" in url for url in urls))

    def test_non_html_is_reported_and_does_not_crash(self):
        result = self._crawl()
        content_errors = [
            error for error in result["errors"] if error["kind"] == "content"
        ]
        self.assertTrue(content_errors)
        self.assertIn("application/json", content_errors[0]["message"])

    def test_start_page_disallowed_by_robots_is_explained(self):
        config = CrawlConfig(
            depth=1, max_pages=1, delay=0, allow_private=True
        )
        result = Crawler(config).crawl(self.base + "private")
        self.assertEqual(result["successes"], 0)
        self.assertGreaterEqual(result["skipped_count"], 1)
        self.assertIn(
            "起始页被 robots.txt 禁止抓取", result["warnings"]
        )

    def test_crawl_can_be_cancelled_during_rate_limit_wait(self):
        config = CrawlConfig(
            depth=2, max_pages=5, delay=5, timeout=5, allow_private=True
        )
        cancel_event = threading.Event()
        first_page_done = threading.Event()

        def on_progress(update):
            if update.get("successes") == 1:
                first_page_done.set()

        crawler = Crawler(
            config, on_progress=on_progress, cancel_event=cancel_event
        )
        result_holder = {}

        def run():
            result_holder["result"] = crawler.crawl(self.base)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(first_page_done.wait(timeout=5))
        cancel_event.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        result = result_holder["result"]
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["successes"], 1)

    def test_progress_callback_receives_updates(self):
        updates = []
        config = CrawlConfig(
            depth=1, max_pages=1, delay=0, allow_private=True
        )
        Crawler(config, on_progress=updates.append).crawl(self.base)
        self.assertTrue(updates)
        self.assertEqual(updates[-1]["phase"], "done")
        self.assertEqual(updates[-1]["successes"], 1)


if __name__ == "__main__":
    unittest.main()
