"""robots.txt 获取、缓存与判定。"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .config import CrawlConfig
from .fetcher import FetchError, Fetcher

logger = logging.getLogger(__name__)
ROBOTS_MAX_BYTES = 512_000


def _robots_url(page_url: str) -> str:
    parsed = urlsplit(page_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))


class RobotsCache:
    """按站点缓存 robots.txt，避免重复下载。"""

    def __init__(self, fetcher: Fetcher, config: CrawlConfig) -> None:
        self.fetcher = fetcher
        self.config = config
        self.user_agent = "PagePulseBot"
        self._cache: dict[str, tuple[RobotFileParser, float]] = {}
        self._origin_loaded: set[str] = set()
        self.warnings: list[str] = []

    def _load(self, page_url: str) -> tuple[RobotFileParser, float]:
        parsed = urlsplit(page_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._origin_loaded:
            return self._cache.get(
                origin, (self._allow_all_parser(), 0.0)
            )

        self._origin_loaded.add(origin)
        parser = RobotFileParser()
        crawl_delay = 0.0
        try:
            response = self.fetcher.get(
                _robots_url(page_url),
                max_bytes=ROBOTS_MAX_BYTES,
                accept="text/plain,*/*;q=0.5",
            )
            if response.status_code in {401, 403}:
                parser.disallow_all = True
            elif response.status_code >= 400:
                parser.allow_all = True
            else:
                text = response.content.decode(
                    response.charset or "utf-8", errors="replace"
                )
                parser.parse(text.splitlines())
                delay = parser.crawl_delay(self.user_agent)
                if isinstance(delay, (int, float)) and delay > 0:
                    crawl_delay = min(float(delay), 10.0)
        except FetchError as exc:
            parser.allow_all = True
            message = f"无法读取 robots.txt（{exc}），本次按允许访问处理"
            if message not in self.warnings:
                self.warnings.append(message)
        except (UnicodeError, ValueError) as exc:
            parser.allow_all = True
            logger.warning("robots.txt 解析失败: %s", exc)

        self._cache[origin] = (parser, crawl_delay)
        return parser, crawl_delay

    @staticmethod
    def _allow_all_parser() -> RobotFileParser:
        parser = RobotFileParser()
        parser.allow_all = True
        return parser

    def can_fetch(self, url: str) -> tuple[bool, float]:
        parser, crawl_delay = self._load(url)
        try:
            allowed = parser.can_fetch(self.user_agent, url)
        except Exception:  # robots 解析异常时保守放行，避免整个任务失败
            logger.exception("robots.txt 判断失败: %s", url)
            allowed = True
        return allowed, crawl_delay
