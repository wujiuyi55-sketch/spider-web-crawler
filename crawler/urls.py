"""URL 规范化与网络安全校验。"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import quote, urlsplit, urlunsplit

MAX_URL_LENGTH = 2_048
ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_HOSTNAMES = {
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
}
# 云厂商元数据地址：即使允许访问内网，也应始终拦截。
BLOCKED_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("fd00:ec2::254"),
}


class UnsafeUrlError(ValueError):
    """URL 不合法，或解析到了不允许访问的地址。"""


def _idna_hostname(hostname: str) -> str:
    try:
        return hostname.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError) as exc:
        raise UnsafeUrlError("域名包含无法识别的字符") from exc


def normalize_url(raw: str, base: str | None = None) -> str:
    """把用户输入整理成规范 URL，并拒绝非 HTTP(S) 地址。

    - 未填写协议时默认补 https://
    - 去掉 #fragment
    - 域名转小写 / IDNA，去掉默认端口
    - 禁止用户名密码、控制字符和超长 URL
    """

    if not isinstance(raw, str):
        raise UnsafeUrlError("网址必须是文本")
    value = raw.strip()
    if not value:
        raise UnsafeUrlError("请输入要采集的网址")
    if len(value) > MAX_URL_LENGTH:
        raise UnsafeUrlError("网址过长")
    if any(char in value for char in "\r\n\t"):
        raise UnsafeUrlError("网址不能包含换行或制表符")

    if base:
        from urllib.parse import urljoin

        value = urljoin(base, value)

    if value.startswith("//"):
        value = "https:" + value
    elif "://" not in value:
        value = "https://" + value.lstrip("/")

    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise UnsafeUrlError("网址格式不正确") from exc
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError("只支持 http:// 或 https:// 网址")
    if not parsed.hostname:
        raise UnsafeUrlError("网址缺少有效的域名")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("网址不能包含用户名或密码")

    hostname = _idna_hostname(parsed.hostname)
    if any(char in hostname for char in " \t\r\n\\/%"):
        raise UnsafeUrlError("域名包含非法字符")
    if hostname.endswith(".") and len(hostname) > 1:
        hostname = hostname[:-1]
    if hostname in BLOCKED_HOSTNAMES:
        raise UnsafeUrlError("该地址被安全策略禁止访问")

    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("网址端口不合法") from exc
    if port == 0:
        raise UnsafeUrlError("网址端口不合法")
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None

    host_for_url = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host_for_url
    if port is not None:
        netloc = f"{host_for_url}:{port}"

    path = quote(parsed.path or "/", safe="/:@!$&'()*+,;=%[]~")
    query = quote(parsed.query, safe="=&?/:;+,%@[]!$'()*~")
    normalized = urlunsplit((scheme, netloc, path, query, ""))
    if len(normalized) > MAX_URL_LENGTH:
        raise UnsafeUrlError("网址过长")
    return normalized


def site_key(url: str) -> str:
    """用于“同站点”判断的键，www 前缀视为同一个站点。"""

    hostname = (urlsplit(url).hostname or "").lower()
    if hostname.startswith("www."):
        return hostname[4:]
    return hostname


def is_same_site(first: str, second: str) -> bool:
    return site_key(first) == site_key(second)


def _address_is_allowed(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address in BLOCKED_IPS:
        return False
    if (
        address.is_unspecified
        or address.is_multicast
        or address.is_link_local
        or address.is_reserved
    ):
        return False
    return True


def ensure_safe_url(url: str, allow_private: bool = False) -> list[str]:
    """解析域名并拦截内网 / 回环 / 云元数据地址。

    返回解析到的 IP 字符串列表。该检查在每次请求和重定向时都会执行，
    但不能完全防御 DNS 重绑定；本工具定位为本地可信使用。
    """

    parsed = urlsplit(url)
    hostname = parsed.hostname
    if not hostname:
        raise UnsafeUrlError("网址缺少有效的域名")
    if hostname in BLOCKED_HOSTNAMES:
        raise UnsafeUrlError("该地址被安全策略禁止访问")

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeUrlError("网址端口不合法") from exc

    try:
        socket.inet_pton(socket.AF_INET, hostname)
        raw_addresses = [hostname]
    except OSError:
        try:
            socket.inet_pton(socket.AF_INET6, hostname)
            raw_addresses = [hostname]
        except OSError:
            try:
                infos = socket.getaddrinfo(
                    hostname, port, type=socket.SOCK_STREAM
                )
            except socket.gaierror as exc:
                raise UnsafeUrlError("域名解析失败，请检查网址是否正确") from exc
            raw_addresses = []
            for info in infos:
                address = info[4][0]
                if address not in raw_addresses:
                    raw_addresses.append(address)

    if not raw_addresses:
        raise UnsafeUrlError("域名解析失败，请检查网址是否正确")

    normalized_addresses: list[str] = []
    for raw_address in raw_addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise UnsafeUrlError("域名解析结果异常") from exc
        if not _address_is_allowed(address):
            raise UnsafeUrlError("出于安全考虑，不能访问该网络地址")
        if not allow_private and not address.is_global:
            raise UnsafeUrlError(
                "该地址属于内网或本机。如确实需要，请勾选“允许访问内网地址”"
            )
        normalized_addresses.append(str(address))
    return normalized_addresses
