"""蛛网爬虫：一个可配置、可导出的小型网页采集引擎。"""

from .config import CrawlConfig
from .engine import Crawler
from .urls import UnsafeUrlError, normalize_url

__all__ = ["CrawlConfig", "Crawler", "UnsafeUrlError", "normalize_url"]
__version__ = "1.0.0"
