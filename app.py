"""蛛网爬虫的 Flask Web 服务。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file

from crawler import __version__
from crawler.config import ConfigError, CrawlConfig
from crawler.jobs import JobManager, JobNotFoundError
from crawler.urls import UnsafeUrlError

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)


def create_app(
    output_dir: str | Path | None = None, max_workers: int | None = None
) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1_000_000
    app.json.ensure_ascii = False

    resolved_output = Path(
        output_dir
        or os.environ.get("CRAWLER_OUTPUT_DIR")
        or BASE_DIR / "crawls"
    )
    workers = max_workers or int(os.environ.get("CRAWLER_WORKERS", "3"))
    manager = JobManager(resolved_output, max_workers=workers)
    app.extensions["job_manager"] = manager
    app.extensions["crawler_output_dir"] = resolved_output

    @app.after_request
    def add_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if response.mimetype == "application/json":
            response.headers["Content-Type"] = "application/json; charset=utf-8"
        return response

    @app.get("/")
    def index():
        return render_template("index.html", version=__version__)

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "version": __version__})

    @app.post("/api/crawl")
    def create_crawl():
        payload: Any = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _error("请求体必须是 JSON 对象", 400)
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            return _error("请输入要采集的网址", 400)
        try:
            config = CrawlConfig.from_payload(payload)
            job = manager.create(url, config)
        except (ConfigError, UnsafeUrlError) as exc:
            return _error(str(exc), 400)
        return jsonify(job), 202

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id: str):
        try:
            return jsonify(manager.get_status(job_id))
        except JobNotFoundError:
            return _error("任务不存在或服务已重启", 404)

    @app.get("/api/jobs/<job_id>/result")
    def job_result(job_id: str):
        try:
            return jsonify(manager.get_result(job_id))
        except JobNotFoundError:
            return _error("任务不存在或服务已重启", 404)
        except RuntimeError:
            return _error("任务尚未结束", 409)
        except FileNotFoundError:
            return _error("任务结果文件不存在", 404)

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel_job(job_id: str):
        try:
            return jsonify(manager.cancel(job_id))
        except JobNotFoundError:
            return _error("任务不存在或服务已重启", 404)

    @app.get("/api/jobs/<job_id>/download/<file_format>")
    def download_job(job_id: str, file_format: str):
        if file_format not in {"json", "csv"}:
            return _error("仅支持下载 JSON 或 CSV", 400)
        try:
            path = manager.download_path(job_id, file_format)
        except JobNotFoundError:
            return _error("任务不存在或服务已重启", 404)
        if path is None:
            return _error("结果尚未生成或文件不可用", 409)
        mimetype = (
            "application/json" if file_format == "json" else "text/csv"
        )
        response = send_file(
            path,
            mimetype=mimetype,
            as_attachment=True,
            download_name=f"crawl-{job_id}.{file_format}",
            max_age=0,
        )
        response.headers["Content-Type"] = (
            f"{mimetype}; charset=utf-8"
        )
        return response

    @app.errorhandler(413)
    def payload_too_large(_error_value):
        return _error("请求体过大", 413)

    @app.errorhandler(404)
    def not_found(_error_value):
        if request.path.startswith("/api/"):
            return _error("接口不存在", 404)
        return render_template("index.html", version=__version__), 404

    def _error(message: str, status: int):
        return jsonify({"error": message}), status

    app.logger.info("采集结果目录：%s", resolved_output)
    return app


app = create_app()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="蛛网网页采集器")
    parser.add_argument(
        "--host",
        default=os.environ.get("CRAWLER_HOST", "127.0.0.1"),
        help="监听地址（默认仅本机）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CRAWLER_PORT", "5000")),
        help="监听端口",
    )
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--debug", action="store_true", help="开启调试模式")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.open:
        threading.Timer(
            1.2, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")
        ).start()
    print(f"\n  蛛网爬虫已启动：http://{args.host}:{args.port}\n")
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
