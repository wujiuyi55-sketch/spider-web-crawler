"""爬取参数及输入校验。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

DEFAULT_USER_AGENT = (
    "PagePulseBot/1.0 (+local web crawler; respectful, low-rate crawling)"
)


class ConfigError(ValueError):
    """用户提供的爬取参数不合法。"""


def _as_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} 必须是整数") from exc
    if not minimum <= number <= maximum:
        raise ConfigError(f"{field} 必须在 {minimum} 到 {maximum} 之间")
    return number


def _as_float(value: Any, field: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} 必须是数字") from exc
    if not minimum <= number <= maximum:
        raise ConfigError(f"{field} 必须在 {minimum:g} 到 {maximum:g} 之间")
    return number


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


@dataclass
class CrawlConfig:
    """一次爬取任务的配置。

    深度 1 表示只抓当前页；深度 2 会跟随当前页里的一层链接；以此类推。
    """

    max_pages: int = 10
    depth: int = 2
    delay: float = 0.6
    timeout: float = 15.0
    respect_robots: bool = True
    same_site: bool = True
    allow_private: bool = False
    verify_ssl: bool = True
    max_text_chars: int = 20_000
    max_bytes: int = 3_000_000
    user_agent: str = DEFAULT_USER_AGENT

    def __post_init__(self) -> None:
        self.max_pages = _as_int(self.max_pages, "最多页面数", 1, 50)
        self.depth = _as_int(self.depth, "采集深度", 1, 3)
        self.delay = _as_float(self.delay, "请求间隔", 0.0, 10.0)
        self.timeout = _as_float(self.timeout, "请求超时", 3.0, 60.0)
        self.respect_robots = _as_bool(self.respect_robots, True)
        self.same_site = _as_bool(self.same_site, True)
        self.allow_private = _as_bool(self.allow_private, False)
        self.verify_ssl = _as_bool(self.verify_ssl, True)
        self.max_text_chars = _as_int(
            self.max_text_chars, "正文长度限制", 1_000, 200_000
        )
        if not isinstance(self.user_agent, str) or not self.user_agent.strip():
            raise ConfigError("User-Agent 不能为空")
        if len(self.user_agent) > 240 or any(
            char in self.user_agent for char in "\r\n"
        ):
            raise ConfigError("User-Agent 不合法")
        self.user_agent = self.user_agent.strip()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CrawlConfig":
        """从网页表单或 API 的 JSON 数据构造配置。"""

        return cls(
            max_pages=payload.get("max_pages", 10),
            depth=payload.get("depth", 2),
            delay=payload.get("delay", 0.6),
            timeout=payload.get("timeout", 15),
            respect_robots=payload.get("respect_robots", True),
            same_site=payload.get("same_site", True),
            allow_private=payload.get("allow_private", False),
            verify_ssl=payload.get("verify_ssl", True),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
