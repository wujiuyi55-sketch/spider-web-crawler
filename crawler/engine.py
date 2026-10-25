"""广度优先爬虫引擎。"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Any, Callable

from .config import CrawlConfig
from .extractor import extract_page
from .fetcher import CrawlCancelled, FetchError, Fetcher
from .robots import RobotsCache
from .urls import ensure_safe_url, is_same_site, normalize_url

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[dict[str, Any]], None]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_html(response_type: str, content: bytes) -> bool:
    if response_type in {"text/html", "application/xhtml+xml"}:
        return True
    if response_type:
        return False
    head = content[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


class Crawler:
    """按深度与页数限制抓取网页，返回结构化结果。

    深度 1：只抓起始页。
    深度 2：抓起始页 + 起始页中的同站点链接。
    深度 3：再向下一层。
    """

    def __init__(
        self,
        config: CrawlConfig | None = None,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.config = config or CrawlConfig()
        self.on_progress = on_progress
        self.cancel_event = cancel_event or threading.Event()
        self._last_request_at: float | None = None

    def _emit(self, payload: dict[str, Any]) -> None:
        if not self.on_progress:
            return
        try:
            self.on_progress(payload)
        except Exception:  # 进度展示失败不应中断采集
            logger.exception("进度回调执行失败")

    def _wait_between_requests(self, delay: float) -> None:
        if self._last_request_at is None or delay <= 0:
            return
        remaining = delay - (time.monotonic() - self._last_request_at)
        if remaining > 0 and self.cancel_event.wait(remaining):
            raise CrawlCancelled("采集已取消")

    def crawl(self, start_url: str) -> dict[str, Any]:
        started_at = _now_iso()
        started = time.monotonic()
        normalized_start = normalize_url(start_url)
        ensure_safe_url(
            normalized_start, allow_private=self.config.allow_private
        )

        fetcher = Fetcher(self.config, self.cancel_event)
        robots = RobotsCache(fetcher, self.config)
        queue: deque[tuple[str, int]] = deque([(normalized_start, 1)])
        queued: set[str] = {normalized_start}
        visited_final: set[str] = set()
        pages: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        skipped_samples: list[dict[str, str]] = []
        skipped_count = 0
        cancelled = False
        limit_reached = False

        self._emit(
            {
                "current_url": normalized_start,
                "pages_done": 0,
                "successes": 0,
                "errors": 0,
                "skipped": 0,
                "message": "正在连接目标网站…",
                "phase": "starting",
            }
        )

        try:
            while queue:
                if self.cancel_event.is_set():
                    cancelled = True
                    break
                if len(pages) + len(errors) >= self.config.max_pages:
                    limit_reached = bool(queue)
                    break

                url, depth = queue.popleft()
                if url in visited_final:
                    continue

                page_delay = self.config.delay
                if self.config.respect_robots:
                    allowed, robots_delay = robots.can_fetch(url)
                    page_delay = max(page_delay, robots_delay)
                    if not allowed:
                        skipped_count += 1
                        if len(skipped_samples) < 50:
                            skipped_samples.append(
                                {"url": url, "reason": "robots.txt 禁止抓取"}
                            )
                        if (
                            url == normalized_start
                            and "起始页被 robots.txt 禁止抓取" not in robots.warnings
                        ):
                            robots.warnings.append("起始页被 robots.txt 禁止抓取")
                        continue

                self._emit(
                    {
                        "current_url": url,
                        "pages_done": len(pages) + len(errors),
                        "successes": len(pages),
                        "errors": len(errors),
                        "skipped": skipped_count,
                        "message": f"正在抓取第 {len(pages) + len(errors) + 1} 个页面",
                        "phase": "fetching",
                    }
                )

                try:
                    self._wait_between_requests(page_delay)
                    try:
                        response = fetcher.get(url)
                    finally:
                        self._last_request_at = time.monotonic()
                except CrawlCancelled:
                    cancelled = True
                    break
                except FetchError as exc:
                    errors.append(
                        {
                            "url": url,
                            "message": str(exc),
                            "depth": depth,
                            "kind": "network",
                        }
                    )
                    self._emit_progress(pages, errors, skipped_count, url, "抓取失败")
                    continue

                if response.status_code < 200 or response.status_code >= 400:
                    errors.append(
                        {
                            "url": url,
                            "message": f"HTTP {response.status_code}",
                            "depth": depth,
                            "kind": "http",
                        }
                    )
                    self._emit_progress(pages, errors, skipped_count, url, "页面返回错误")
                    continue

                if not _is_html(response.content_type, response.content):
                    label = response.content_type or "未知类型"
                    errors.append(
                        {
                            "url": url,
                            "message": f"跳过非 HTML 内容（{label}）",
                            "depth": depth,
                            "kind": "content",
                        }
                    )
                    self._emit_progress(pages, errors, skipped_count, url, "跳过非 HTML 内容")
                    continue

                try:
                    page = extract_page(
                        response.content,
                        response.final_url,
                        content_type=response.content_type,
                        max_text_chars=self.config.max_text_chars,
                        from_encoding=response.charset,
                    )
                except Exception as exc:
                    logger.exception("解析页面失败: %s", response.final_url)
                    errors.append(
                        {
                            "url": url,
                            "message": f"页面解析失败：{exc.__class__.__name__}",
                            "depth": depth,
                            "kind": "parse",
                        }
                    )
                    self._emit_progress(pages, errors, skipped_count, url, "页面解析失败")
                    continue

                page.update(
                    {
                        "requested_url": url,
                        "status": response.status_code,
                        "depth": depth,
                        "elapsed_ms": response.elapsed_ms,
                        "size_bytes": len(response.content),
                        "response_truncated": response.truncated,
                        "redirected": response.final_url != url,
                        "redirect_chain": response.redirect_chain,
                        "fetched_at": _now_iso(),
                    }
                )
                pages.append(page)
                visited_final.add(response.final_url)
                visited_final.add(url)

                if depth < self.config.depth:
                    self._enqueue_links(
                        page,
                        depth,
                        queue,
                        queued,
                        visited_final,
                        limit=max(20, self.config.max_pages * 20),
                    )

                self._emit_progress(
                    pages,
                    errors,
                    skipped_count,
                    response.final_url,
                    f"已采集「{page['title'][:60]}」",
                )
        finally:
            fetcher.close()

        status_counts = Counter(str(page["status"]) for page in pages)
        duration_ms = int((time.monotonic() - started) * 1000)
        warnings = list(dict.fromkeys(robots.warnings))
        if limit_reached:
            warnings.append(
                f"已达到 {self.config.max_pages} 个页面的采集上限，队列中还有待抓取链接"
            )

        result: dict[str, Any] = {
            "start_url": normalized_start,
            "pages": pages,
            "errors": errors,
            "warnings": warnings,
            "skipped": skipped_samples,
            "skipped_count": skipped_count,
            "status_counts": dict(status_counts),
            "pages_done": len(pages) + len(errors),
            "successes": len(pages),
            "error_count": len(errors),
            "cancelled": cancelled,
            "limit_reached": limit_reached,
            "started_at": started_at,
            "finished_at": _now_iso(),
            "duration_ms": duration_ms,
            "config": self.config.to_dict(),
        }
        self._emit(
            {
                "current_url": "",
                "pages_done": result["pages_done"],
                "successes": len(pages),
                "errors": len(errors),
                "skipped": skipped_count,
                "message": "采集已取消" if cancelled else "采集完成",
                "phase": "done",
            }
        )
        return result

    def _emit_progress(
        self,
        pages: list[dict[str, Any]],
        errors: list[dict[str, Any]],
        skipped_count: int,
        current_url: str,
        message: str,
    ) -> None:
        self._emit(
            {
                "current_url": current_url,
                "pages_done": len(pages) + len(errors),
                "successes": len(pages),
                "errors": len(errors),
                "skipped": skipped_count,
                "message": message,
                "phase": "fetching",
            }
        )

    def _enqueue_links(
        self,
        page: dict[str, Any],
        depth: int,
        queue: deque[tuple[str, int]],
        queued: set[str],
        visited_final: set[str],
        limit: int,
    ) -> None:
        for link in page["links"]:
            if len(queue) >= limit:
                break
            if link.get("nofollow"):
                continue
            candidate = link["url"]
            if candidate in queued or candidate in visited_final:
                continue
            if self.config.same_site and not is_same_site(
                page["url"], candidate
            ):
                continue
            queued.add(candidate)
            queue.append((candidate, depth + 1))
