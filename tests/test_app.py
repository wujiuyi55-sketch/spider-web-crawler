import http.client
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import create_app


class SmallSite(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        return

    def do_GET(self):
        if self.path == "/":
            body = (
                b"<html><head><title>API Test</title>"
                b'<meta name="description" content="hello"></head>'
                b"<body><h1>Hello</h1><p>\xe4\xbd\xa0\xe5\xa5\xbd</p></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


class WebAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SmallSite)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()
        # 等待测试服务器真正能处理 HTTP 请求，避免 CI 上的启动竞态。
        for _ in range(50):
            try:
                connection = http.client.HTTPConnection(
                    "127.0.0.1", cls.port, timeout=0.5
                )
                connection.request("GET", "/")
                response = connection.getresponse()
                response.read()
                connection.close()
                break
            except OSError:
                time.sleep(0.02)
        else:
            raise RuntimeError("test HTTP server did not become ready")
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.app = create_app(Path(cls.tempdir.name), max_workers=1)
        cls.app.testing = True

    @classmethod
    def tearDownClass(cls):
        cls.app.extensions["job_manager"].shutdown()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.tempdir.cleanup()

    def setUp(self):
        self.client = self.app.test_client()

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")
        self.assertEqual(
            response.headers["Content-Type"], "application/json; charset=utf-8"
        )

    def test_invalid_url_returns_400(self):
        response = self.client.post(
            "/api/crawl", json={"url": "ftp://example.com/file"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("http", response.get_json()["error"])

    def test_private_url_requires_opt_in(self):
        response = self.client.post(
            "/api/crawl",
            json={"url": f"http://127.0.0.1:{self.port}/", "depth": 1},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("内网", response.get_json()["error"])

    def test_full_job_flow_and_downloads(self):
        response = self.client.post(
            "/api/crawl",
            json={
                "url": f"http://127.0.0.1:{self.port}/",
                "depth": 1,
                "max_pages": 1,
                "delay": 0,
                "allow_private": True,
            },
        )
        self.assertEqual(response.status_code, 202)
        job = response.get_json()
        job_id = job["id"]

        status = None
        for _ in range(100):
            status_response = self.client.get(f"/api/jobs/{job_id}")
            self.assertEqual(status_response.status_code, 200)
            status = status_response.get_json()
            if status["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        self.assertIsNotNone(status)
        self.assertEqual(status["status"], "completed", msg=status)
        self.assertEqual(status["successes"], 1)
        self.assertTrue(status["result_available"])

        result_response = self.client.get(f"/api/jobs/{job_id}/result")
        self.assertEqual(result_response.status_code, 200)
        result = result_response.get_json()
        self.assertEqual(result["pages"][0]["title"], "API Test")
        self.assertEqual(result["pages"][0]["description"], "hello")

        json_download = self.client.get(
            f"/api/jobs/{job_id}/download/json"
        )
        self.assertEqual(json_download.status_code, 200)
        self.assertIn("API Test", json_download.data.decode("utf-8"))
        self.assertIn(
            "charset=utf-8", json_download.headers["Content-Type"]
        )
        json_download.close()

        csv_download = self.client.get(f"/api/jobs/{job_id}/download/csv")
        self.assertEqual(csv_download.status_code, 200)
        csv_text = csv_download.data.decode("utf-8-sig")
        self.assertIn("API Test", csv_text)
        csv_download.close()

    def test_missing_job_returns_404(self):
        response = self.client.get("/api/jobs/not-a-job")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
