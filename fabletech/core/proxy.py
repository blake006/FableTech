"""代理服务：URL 解析、批量导入、掩码显示、连通测试（可注入连接器）、ip-api 地理查询。

红线：日志/UI 中代理 URL 必须掩码；密码只以加密形式落库。
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote, unquote, urlsplit

from .logger import mask_credentials

PROXY_TYPES = {"http", "https", "socks5", "socks5h", "socks4"}


class ProxyParseError(ValueError):
    """代理 URL 解析失败。"""


@dataclass(frozen=True)
class ProxyInfo:
    type: str       # http | https | socks5 | socks5h | socks4
    host: str
    port: int
    username: str | None
    password: str | None

    def masked_url(self) -> str:
        """socks5://u***:p***@host:port —— 用于日志/UI 显示。"""
        return mask_credentials(self.url())

    def url(self) -> str:
        auth = ""
        if self.username:
            u = quote(self.username, safe="")
            p = quote(self.password or "", safe="")
            auth = f"{u}:{p}@"
        return f"{self.type}://{auth}{self.host}:{self.port}"


def parse_proxy_line(line: str) -> ProxyInfo:
    """解析单行代理：支持
    - scheme://user:pass@host:port
    - scheme://host:port
    - host:port（默认 http）
    - user:pass@host:port（默认 http）
    """
    text = line.strip()
    if not text or text.startswith("#"):
        raise ProxyParseError("空行或注释行")
    if "://" not in text:
        # 无协议：按 host:port 或 user:pass@host:port 处理，默认 http
        text = "http://" + text
    try:
        parts = urlsplit(text)
    except ValueError as e:
        raise ProxyParseError(f"URL 非法: {line}") from e
    scheme = (parts.scheme or "").lower()
    if scheme not in PROXY_TYPES:
        raise ProxyParseError(f"不支持的协议类型: {scheme}")
    if not parts.hostname or parts.port is None:
        raise ProxyParseError(f"缺少主机或端口: {line}")
    username = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None
    return ProxyInfo(type=scheme, host=parts.hostname, port=parts.port,
                     username=username, password=password)


def parse_proxy_bulk(text: str) -> tuple[list[ProxyInfo], list[tuple[int, str, str]]]:
    """批量解析。返回 (成功列表, 失败列表[(行号, 原文, 原因)])，异常行跳过。"""
    ok: list[ProxyInfo] = []
    bad: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.strip().startswith("#"):
            continue
        try:
            ok.append(parse_proxy_line(line))
        except ProxyParseError as e:
            bad.append((i, line.strip(), str(e)))
    return ok, bad


# ---------- 连通测试（连接器可注入，便于 mock） ----------

Connector = Callable[[str, int, float], float]  # (host, port, timeout) -> 延迟ms


def tcp_connector(host: str, port: int, timeout: float) -> float:
    """真实 TCP 连通测试：返回延迟毫秒；失败抛 OSError。"""
    import time

    start = time.monotonic()
    with socket.create_connection((host, port), timeout=timeout):
        pass
    return (time.monotonic() - start) * 1000


def test_proxy(proxy: ProxyInfo, timeout: float = 8.0,
               connector: Connector | None = None) -> tuple[bool, float | None, str]:
    """连通性测试。返回 (是否可用, 延迟ms, 中文提示)。

    connector 可注入 mock（测试用）；默认真实 TCP 连接。
    """
    connect = connector or tcp_connector
    try:
        latency = connect(proxy.host, proxy.port, timeout)
        return True, round(latency, 1), "连接成功"
    except OSError as e:
        return False, None, f"无法连接 {proxy.host}:{proxy.port}（{e.strerror or e}）"
    except Exception as e:  # mock 连接器异常统一收敛
        return False, None, f"连通测试失败：{e}"


# ---------- ip-api 地理查询 ----------

def query_geo(ip: str, timeout: float = 8.0,
              fetcher: Callable[[str, float], dict] | None = None) -> dict:
    """查询 IP 出口地理（ip-api.com，免费档，仅 HTTP/无 key）。

    返回 {country, countryCode, city, isp, query}；失败时 country/city 为空串。
    fetcher 可注入 mock。
    """
    fetch = fetcher or _default_geo_fetcher
    try:
        data = fetch(ip, timeout)
    except Exception as e:
        return {"country": "", "countryCode": "", "city": "",
                "isp": "", "query": ip, "error": f"地理查询失败：{e}"}
    return {
        "country": data.get("country", ""),
        "countryCode": data.get("countryCode", ""),
        "city": data.get("city", ""),
        "isp": data.get("isp", ""),
        "query": data.get("query", ip),
    }


def _default_geo_fetcher(ip: str, timeout: float) -> dict:
    """ip-api.com /json/{ip}（免费、无需 key；仅用户显式操作时调用）。"""
    import urllib.request

    url = f"http://ip-api.com/json/{quote(ip, safe='')}?fields=status,country,countryCode,city,isp,query"
    ctx = ssl.create_default_context()  # http 不用，但保持导入一致
    del ctx
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        import json
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("status") != "success":
        raise RuntimeError(payload.get("message", "查询失败"))
    return payload


def suggest_locale(country_code: str) -> str:
    """国家码 → locale/时区建议（用于档案与代理地理对齐）。"""
    mapping = {
        "US": "en-US", "GB": "en-GB", "CA": "en-CA", "AU": "en-AU",
        "DE": "de-DE", "FR": "fr-FR", "JP": "ja-JP", "KR": "ko-KR",
        "SG": "en-SG", "IN": "en-IN", "BR": "pt-BR", "CN": "zh-CN",
    }
    return mapping.get((country_code or "").upper(), "en-US")
