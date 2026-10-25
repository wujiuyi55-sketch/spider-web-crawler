"""命令行采集入口。

示例：
    python cli.py https://example.com --depth 2 --max-pages 10
    python cli.py example.com --json result.json --csv pages.csv
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from crawler.config import CrawlConfig
from crawler.engine import Crawler
from crawler.exporters import save_csv, save_json
from crawler.urls import UnsafeUrlError

BASE_DIR = Path(__file__).resolve().parent


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="输入网址，采集网页标题、正文、链接、图片与表格。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("url", help="要采集的网址")
    parser.add_argument("--depth", type=int, default=2, help="采集深度（1-3）")
    parser.add_argument(
        "--max-pages", type=int, default=10, help="最多采集页面数（1-50）"
    )
    parser.add_argument(
        "--delay", type=float, default=0.6, help="请求间隔秒数（0-10）"
    )
    parser.add_argument(
        "--timeout", type=float, default=15.0, help="请求超时秒数（3-60）"
    )
    parser.add_argument(
        "--any-domain", action="store_true", help="允许跟随站外链接"
    )
    parser.add_argument(
        "--allow-private", action="store_true", help="允许访问本机/内网地址"
    )
    parser.add_argument(
        "--ignore-robots", action="store_true", help="忽略 robots.txt"
    )
    parser.add_argument(
        "--insecure", action="store_true", help="忽略 HTTPS 证书错误"
    )
    parser.add_argument("--json", metavar="PATH", help="JSON 输出路径")
    parser.add_argument("--csv", metavar="PATH", help="CSV 输出路径")
    parser.add_argument("--quiet", action="store_true", help="不输出进度")
    return parser


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args()
    try:
        config = CrawlConfig(
            max_pages=args.max_pages,
            depth=args.depth,
            delay=args.delay,
            timeout=args.timeout,
            same_site=not args.any_domain,
            allow_private=args.allow_private,
            respect_robots=not args.ignore_robots,
            verify_ssl=not args.insecure,
        )
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2

    last_shown_url = {"value": None}

    def on_progress(update):
        if args.quiet:
            return
        current = update.get("current_url") or ""
        if update.get("phase") == "fetching" and current != last_shown_url["value"]:
            last_shown_url["value"] = current
            print(
                f"[{update.get('pages_done', 0)}/{config.max_pages}] {current}",
                file=sys.stderr,
            )

    print(f"开始采集：{args.url}", file=sys.stderr)
    crawler = Crawler(config=config, on_progress=on_progress)
    try:
        result = crawler.crawl(args.url)
    except UnsafeUrlError as exc:
        print(f"网址错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已手动中断。", file=sys.stderr)
        return 130

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    default_dir = BASE_DIR / "crawls" / f"cli-{stamp}"
    write_defaults = not args.json and not args.csv
    json_path = (
        Path(args.json)
        if args.json
        else (default_dir / "pages.json" if write_defaults else None)
    )
    csv_path = (
        Path(args.csv)
        if args.csv
        else (default_dir / "pages.csv" if write_defaults else None)
    )
    try:
        if json_path:
            save_json(result, json_path)
        if csv_path:
            save_csv(result["pages"], csv_path)
    except OSError as exc:
        print(f"保存结果失败：{exc}", file=sys.stderr)
        return 1

    print(
        f"完成：成功 {result['successes']} 页，失败 {result['error_count']} 页，"
        f"跳过 {result['skipped_count']} 个链接，用时 "
        f"{result['duration_ms'] / 1000:.1f} 秒。",
        file=sys.stderr,
    )
    for page in result["pages"]:
        print(f"  [{page['status']}] {page['title']}  {page['url']}")
    for warning in result["warnings"]:
        print(f"  提示：{warning}", file=sys.stderr)
    for error in result["errors"][:10]:
        print(f"  失败：{error['url']} — {error['message']}", file=sys.stderr)
    if json_path:
        print(f"JSON：{json_path.resolve()}", file=sys.stderr)
    if csv_path:
        print(f"CSV ：{csv_path.resolve()}", file=sys.stderr)
    return 0 if result["successes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
