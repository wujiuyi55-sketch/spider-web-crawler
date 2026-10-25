"""带安全校验、重定向上限和响应大小限制的 HTTP 客户端。"""

from __future__ import annotations

import codecs
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests

from .config import CrawlConfig
from .urls import ensure_safe_url, normalize_url

_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([^;\"'\s]+)", re.IGNORECASE)
MAX_REDIRECTS = 5


class FetchError(RuntimeError):
    """网络请求失败，消息可直接展示给用户。"""


class CrawlCancelled(RuntimeError):
    """用户取消了采集。"""


@dataclass
class FetchResponse:
    requested_url: str
    final_url: str
    status_code: int
    content: bytes
    content_type: str
    charset: str | None
    truncated: bool
    elapsed_ms: int
    redirect_chain: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)


def _parse_charset(content_type: str) -> str | None:
    match = _CHARSET_RE.search(content_type or "")
    if not match:
        return None
    candidate = match.group(1).strip().lower()
    try:
        codecs.lookup(candidate)
    except LookupError:
        return None
    return candidate


class Fetcher:
    def __init__(
        self,
        config: CrawlConfig,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self.cancel_event = cancel_event or threading.Event()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.user_agent,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.8,"
                    "text/plain;q=0.5,*/*;q=0.2"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "close",
            }
        )

    def close(self) -> None:
        self.session.close()

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise CrawlCancelled("采集已取消")

    def get(
        self,
        url: str,
        *,
        max_bytes: int | None = None,
        accept: str | None = None,
    ) -> FetchResponse:
        """GET 一个 URL，手动处理重定向并在每一跳校验目标地址。"""

        self._check_cancelled()
        max_bytes = max_bytes or self.config.max_bytes
        current = normalize_url(url)
        redirects: list[str] = []
        started = time.monotonic()

        for _ in range(MAX_REDIRECTS + 1):
            ensure_safe_url(current, allow_private=self.config.allow_private)
            headers = {"Accept": accept} if accept else {}
            response = None
            for attempt in range(2):
                try:
                    response = self.session.get(
                        current,
                        timeout=(
                            min(10.0, self.config.timeout),
                            self.config.timeout,
                        ),
                        allow_redirects=False,
                        stream=True,
                        verify=self.config.verify_ssl,
                        headers=headers,
                    )
                    break
                except requests.exceptions.SSLError as exc:
                    raise FetchError(
                        "HTTPS 证书校验失败。若你信任该站点，可在高级设置中忽略证书错误"
                    ) from exc
                except requests.exceptions.Timeout as exc:
                    raise FetchError(
                        f"请求超时（超过 {self.config.timeout:g} 秒）"
                    ) from exc
                except requests.exceptions.ConnectionError as exc:
                    if attempt == 0:
                        # 本地服务或长连接偶尔会刚建立就断开，换一条连接重试一次。
                        self.session.close()
                        if self.cancel_event.wait(0.25):
                            raise CrawlCancelled("采集已取消")
                        continue
                    raise FetchError(
                        "无法连接到服务器，请检查网址或网络"
                    ) from exc
                except requests.exceptions.RequestException as exc:
                    raise FetchError(f"请求失败：{exc.__class__.__name__}") from exc

            if response is None:
                raise FetchError("无法连接到服务器，请检查网址或网络")

            with response:
                location = response.headers.get("Location")
                if response.status_code in {301, 302, 303, 307, 308} and location:
                    next_url = normalize_url(urljoin(current, location))
                    if next_url in redirects or next_url == current:
                        raise FetchError("检测到循环重定向")
                    redirects.append(current)
                    current = next_url
                    if len(redirects) > MAX_REDIRECTS:
                        raise FetchError("重定向次数过多")
                    continue

                content = bytearray()
                truncated = False
                content_type_header = response.headers.get("Content-Type", "")
                try:
                    for chunk in response.iter_content(chunk_size=65_536):
                        self._check_cancelled()
                        if not chunk:
                            continue
                        remaining = max_bytes - len(content)
                        if remaining <= 0:
                            truncated = True
                            break
                        if len(chunk) > remaining:
                            content.extend(chunk[:remaining])
                            truncated = True
                            break
                        content.extend(chunk)
                except requests.exceptions.RequestException as exc:
                    raise FetchError("读取页面内容时连接中断") from exc

                elapsed_ms = int((time.monotonic() - started) * 1000)
                media_type = content_type_header.split(";", 1)[0].strip().lower()
                return FetchResponse(
                    requested_url=url,
                    final_url=current,
                    status_code=response.status_code,
                    content=bytes(content),
                    content_type=media_type,
                    charset=_parse_charset(content_type_header),
                    truncated=truncated,
                    elapsed_ms=elapsed_ms,
                    redirect_chain=redirects,
                    headers={
                        "content_type": content_type_header,
                        "server": response.headers.get("Server", ""),
                    },
                )

        raise FetchError("重定向次数过多")
