"""代理服务：URL 解析、批量导入、掩码显示、连通测试（可注入连接器）、ip-api 地理查询。

支持协议：
  - http / https / socks5 / socks5h / socks4 —— 浏览器可直接使用
  - hy2 / hysteria2（Hysteria2，QUIC） / anytls（AnyTLS，TLS 混淆）
    —— 浏览器不能直连，由 core/relay.py 通过 mihomo 本地中转为 SOCKS5

红线：日志/UI 中代理 URL 必须掩码；密码只以加密形式落库。
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qsl, quote, unquote, urlsplit

from .logger import mask_credentials

PROXY_TYPES = {"http", "https", "socks5", "socks5h", "socks4"}

# 需要本地中转的协议（浏览器内核不直连这些传输层协议）
RELAY_TYPES = {"hy2", "hysteria2", "anytls"}

SUPPORTED_TYPES = PROXY_TYPES | RELAY_TYPES

# hysteria2 别名归一化
_HY2_ALIASES = {"hy2": "hysteria2", "hysteria2": "hysteria2",
                "anytls": "anytls"}


class ProxyParseError(ValueError):
    """代理 URL 解析失败。"""


@dataclass(frozen=True)
class ProxyInfo:
    type: str       # http | https | socks5 | socks5h | socks4 | hy2 | hysteria2 | anytls
    host: str
    port: int
    username: str | None
    password: str | None
    # ---- 中转协议扩展参数（hy2/anytls）----
    sni: str | None = None            # TLS SNI（缺省同 host）
    insecure: bool = False            # 跳过证书校验（insecure=1）
    obfs: str | None = None           # hy2 混淆：salamander
    obfs_password: str | None = None  # 混淆密码
    extra: dict = field(default_factory=dict)  # 其余原始参数（alpn 等）

    @property
    def is_relay(self) -> bool:
        return self.type in RELAY_TYPES

    def masked_url(self) -> str:
        """socks5://u***:p***@host:port —— 用于日志/UI 显示。"""
        return mask_credentials(self.url())

    def url(self) -> str:
        if self.type in RELAY_TYPES:
            # hy2/anytls 的 userinfo 整体是 auth（无 user:pass 之分）
            auth = ""
            if self.username or self.password:
                u = quote(self.username or "", safe="")
                p = quote(self.password or "", safe="")
                auth = f"{u}:{p}@" if self.username else f"{p}@"
            return f"{self.type}://{auth}{self.host}:{self.port}"
        auth = ""
        if self.username:
            u = quote(self.username, safe="")
            p = quote(self.password or "", safe="")
            auth = f"{u}:{p}@"
        return f"{self.type}://{auth}{self.host}:{self.port}"

    def extra_to_dict(self) -> dict:
        """扩展参数 → dict（存储层序列化用）。"""
        d: dict = {}
        if self.sni:
            d["sni"] = self.sni
        if self.insecure:
            d["insecure"] = True
        if self.obfs:
            d["obfs"] = self.obfs
        if self.obfs_password:
            d["obfs_password"] = self.obfs_password
        if self.extra:
            d["extra"] = dict(self.extra)
        return d

    @classmethod
    def from_extra(cls, type: str, host: str, port: int,
                   username: str | None, password: str | None,
                   extra: dict | None) -> "ProxyInfo":
        """从存储的扩展参数 dict 重建（extra 为 extra_to_dict 的逆）。"""
        extra = extra or {}
        return cls(
            type=type, host=host, port=port, username=username,
            password=password,
            sni=extra.get("sni"),
            insecure=bool(extra.get("insecure", False)),
            obfs=extra.get("obfs"),
            obfs_password=extra.get("obfs_password"),
            extra=dict(extra.get("extra") or {}),
        )


def parse_proxy_line(line: str) -> ProxyInfo:
    """解析单行代理：支持
    - scheme://user:pass@host:port
    - scheme://host:port
    - host:port（默认 http）
    - user:pass@host:port（默认 http）
    - hy2://auth@host:port/?sni=x&insecure=1&obfs=salamander&obfs-password=y
    - hysteria2://... / anytls://...（同上，含查询参数）
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
    if scheme not in SUPPORTED_TYPES:
        raise ProxyParseError(f"不支持的协议类型: {scheme}")
    if not parts.hostname or parts.port is None:
        raise ProxyParseError(f"缺少主机或端口: {line}")
    username = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None

    if scheme in RELAY_TYPES:
        # hy2/anytls URI 的 userinfo 整体是 auth 串（无 user:pass 之分）；
        # 仅有 userinfo 无冒号时归一化到 password
        if username and not password:
            password, username = username, None
        # 解析查询参数：sni / insecure / obfs / obfs-password / 其余进 extra
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        sni = q.pop("sni", None) or q.pop("peer", None)
        insecure = _truthy(q.pop("insecure", None) or q.pop("allowInsecure", None))
        obfs = q.pop("obfs", None)
        obfs_password = q.pop("obfs-password", None) or q.pop("obfsParam", None)
        return ProxyInfo(
            type=_HY2_ALIASES.get(scheme, scheme),
            host=parts.hostname, port=parts.port,
            username=username, password=password,
            sni=sni, insecure=insecure, obfs=obfs,
            obfs_password=obfs_password, extra=q,
        )

    return ProxyInfo(type=scheme, host=parts.hostname, port=parts.port,
                     username=username, password=password)


def _truthy(value: str | None) -> bool:
    """查询参数真值判定：1/true/yes/on（大小写不敏感）为真。"""
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


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
    注意：对 hy2/anytls 这是「服务器可达性」测试，完整链路需
    test_relay_proxy（经本地中转实测）。
    """
    connect = connector or tcp_connector
    try:
        latency = connect(proxy.host, proxy.port, timeout)
        return True, round(latency, 1), "连接成功"
    except OSError as e:
        return False, None, f"无法连接 {proxy.host}:{proxy.port}（{e.strerror or e}）"
    except Exception as e:  # mock 连接器异常统一收敛
        return False, None, f"连通测试失败：{e}"


def socks5_http_get(host: str, port: int, url: str,
                    timeout: float = 15.0) -> bytes:
    """经本地 SOCKS5 端口发起 HTTP GET（最小 SOCKS5 客户端，无第三方依赖）。

    用于验证中转链路（mihomo 本地端口 → 上游）并获取出口信息。
    """
    import time

    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        # 1) 握手：无认证
        sock.sendall(b"\x05\x01\x00")
        resp = _recv_exact(sock, 2)
        if resp[0] != 0x05 or resp[1] != 0x00:
            raise OSError("SOCKS5 握手被拒绝")
        # 2) CONNECT 目标（域名形式 ATYP=3）
        target = urlsplit(url)
        t_host = target.hostname or "ip-api.com"
        t_port = target.port or 80
        host_b = t_host.encode("ascii")
        req = (b"\x05\x01\x00\x03" + bytes([len(host_b)]) + host_b
               + t_port.to_bytes(2, "big"))
        sock.sendall(req)
        head = _recv_exact(sock, 4)
        if head[1] != 0x00:
            raise OSError(f"SOCKS5 CONNECT 失败 code={head[1]}")
        atyp = head[3]
        if atyp == 0x01:
            _recv_exact(sock, 4)
        elif atyp == 0x03:
            ln = sock.recv(1)[0]
            _recv_exact(sock, ln)
        elif atyp == 0x04:
            _recv_exact(sock, 16)
        _recv_exact(sock, 2)
        # 3) HTTP GET
        path = target.path or "/"
        if target.query:
            path += "?" + target.query
        http = (f"GET {path} HTTP/1.1\r\n"
                f"Host: {t_host}\r\n"
                f"User-Agent: FableTech\r\n"
                f"Connection: close\r\n\r\n").encode("ascii")
        sock.sendall(http)
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)
    finally:
        sock.close()


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("SOCKS5 响应不完整（连接被关闭）")
        buf += chunk
    return buf


def test_relay_proxy(local_host: str, local_port: int,
                     timeout: float = 15.0) -> tuple[bool, str]:
    """经本地中转实测链路 + 出口地理。返回 (是否可用, 提示/地理信息)。

    通过 socks5://local_host:local_port 请求 ip-api，成功即链路通，
    并解析出口国家/城市。
    """
    import json as _json

    url = ("http://ip-api.com/json/?fields=status,country,countryCode,"
           "city,isp,query")
    try:
        raw = socks5_http_get(local_host, local_port, url, timeout=timeout)
    except Exception as e:
        return False, f"经本地中转访问失败：{e}"
    body = raw.split(b"\r\n\r\n", 1)[-1]
    try:
        data = _json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return False, "出口地理响应解析失败"
    if data.get("status") != "success":
        return False, "出口查询失败（中转可能不通）"
    return True, (f"链路通 · 出口 {data.get('country', '')}"
                  f" {data.get('city', '')}（{data.get('isp', '')}）")


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
