from __future__ import annotations

from typing import Optional
from urllib.parse import unquote, urlsplit, urlunsplit


def normalize_proxy_url(proxy_url: Optional[str]) -> Optional[str]:
    """将 socks5:// 规范化为 socks5h://，避免本地 DNS 泄漏。"""
    if proxy_url is None:
        return None

    value = str(proxy_url).strip()
    if not value:
        return None

    parts = urlsplit(value)
    if (parts.scheme or "").lower() == "socks5":
        parts = parts._replace(scheme="socks5h")
        return urlunsplit(parts)
    return value


def build_requests_proxy_config(proxy_url: Optional[str]) -> Optional[dict[str, str]]:
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def build_playwright_proxy_config(proxy_url: Optional[str]) -> Optional[dict[str, str]]:
    if not proxy_url:
        return None

    parts = urlsplit(proxy_url)
    if not parts.scheme or not parts.hostname or parts.port is None:
        return {"server": proxy_url}

    config = {"server": f"{parts.scheme}://{parts.hostname}:{parts.port}"}
    if parts.username:
        config["username"] = unquote(parts.username)
    if parts.password:
        config["password"] = unquote(parts.password)
    return config


class ProxyBandwidthExhausted(RuntimeError):
    """Proxy đã hết băng thông (HTTP 402 Payment Required)."""

    def __init__(self, proxy_url: str):
        self.proxy_url = proxy_url
        super().__init__(f"Proxy bandwidth exhausted (402): {proxy_url}")


import re as _re

_WEBSHARE_RE = _re.compile(
    r"^(?P<ip>\d{1,3}(?:\.\d{1,3}){3}):(?P<port>\d+):(?P<user>[^:]+):(?P<pass>.+)$"
)


def convert_webshare_proxy(line: str) -> Optional[str]:
    """Chuyển đổi Webshare format IP:PORT:USER:PASS → http://USER:PASS@IP:PORT.

    Nếu dòng đã có scheme (http://, socks5://...) thì giữ nguyên và normalize.
    Trả về None nếu dòng rỗng hoặc không hợp lệ.
    """
    text = str(line or "").strip()
    if not text:
        return None

    # Đã có scheme → giữ nguyên, chỉ normalize socks5
    if "://" in text:
        return normalize_proxy_url(text)

    m = _WEBSHARE_RE.match(text)
    if m:
        ip = m.group("ip")
        port = m.group("port")
        user = m.group("user")
        pwd = m.group("pass")
        return f"http://{user}:{pwd}@{ip}:{port}"

    # Fallback: nếu có đúng 3 dấu ":" thì thử parse
    parts = text.split(":")
    if len(parts) == 4:
        ip, port, user, pwd = parts
        return f"http://{user.strip()}:{pwd.strip()}@{ip.strip()}:{port.strip()}"

    return None
