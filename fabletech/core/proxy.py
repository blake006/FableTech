"""代理服务：URL 解析、批量导入、掩码显示、连通测试（可注入连接器）、ip-api 地理查询。

支持协议：
  - http / https / socks5 / socks5h / socks4 —— 浏览器可直接使用
  - hy2 / hysteria2（Hysteria2，QUIC） / anytls（AnyTLS，TLS 混淆）
  - vmess / vless / trojan / ss（机场订阅常见协议族，经 mihomo 中转）
    —— 浏览器不能直连，由 core/relay.py 通过 mihomo 本地中转为 SOCKS5

红线：日志/UI 中代理 URL 必须掩码；密码只以加密形式落库。
"""

from __future__ import annotations

import base64
import binascii
import json as _json
import socket
import ssl
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qsl, quote, unquote, urlsplit

from .logger import mask_credentials

PROXY_TYPES = {"http", "https", "socks5", "socks5h", "socks4"}

# 需要本地中转的协议（浏览器内核不直连这些传输层协议）
RELAY_TYPES = {"hy2", "hysteria2", "anytls",
               "vmess", "vless", "trojan", "ss", "shadowsocks"}

SUPPORTED_TYPES = PROXY_TYPES | RELAY_TYPES

# 类型归一化（别名 → mihomo type 名）
_HY2_ALIASES = {"hy2": "hysteria2", "hysteria2": "hysteria2",
                "anytls": "anytls", "ss": "ss", "shadowsocks": "ss"}


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
    rest_after_scheme = text.split("://", 1)[1] if "://" in text else text
    # ss 整体 base64 形态（SIP002 旧式）与 vmess（base64 JSON 载荷）
    # 都没有标准 host:port 结构，必须在通用校验前交给专用解析器
    if scheme in ("ss", "shadowsocks") and "@" not in rest_after_scheme:
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        dialer = q.pop("dialer", None)
        return _parse_ss(line, text, q, None, False, dialer)
    if scheme == "vmess":
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        return _parse_vmess(line, text, q, None, False)
    if not parts.hostname or parts.port is None:
        raise ProxyParseError(f"缺少主机或端口: {line}")
    username = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None

    if scheme in RELAY_TYPES:
        # hy2/anytls URI 的 userinfo 整体是 auth（无 user:pass 之分）；
        # 仅有 userinfo 无冒号时归一化到 password
        if username and not password:
            password, username = username, None
        # 解析查询参数：sni / insecure / obfs / obfs-password / 其余进 extra
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        sni = q.pop("sni", None) or q.pop("peer", None)
        insecure = _truthy(q.pop("insecure", None) or q.pop("allowInsecure", None))
        obfs = q.pop("obfs", None)
        obfs_password = q.pop("obfs-password", None) or q.pop("obfsParam", None)
        # 预代理链：dialer=<代理ID>（引用代理池另一条代理作为前置）
        dialer = q.pop("dialer", None)
        norm = _HY2_ALIASES.get(scheme, scheme)

        if scheme in ("vmess",):
            return _parse_vmess(line, text, q, sni, insecure)
        if scheme in ("vless", "trojan"):
            return ProxyInfo(
                type=norm, host=parts.hostname, port=parts.port,
                username=username, password=password,
                sni=sni, insecure=insecure, obfs=obfs,
                obfs_password=obfs_password,
                extra={**q, **({"dialer": dialer} if dialer else {})})
        if scheme in ("ss", "shadowsocks"):
            return _parse_ss(line, text, q, sni, insecure, dialer)
        return ProxyInfo(
            type=norm, host=parts.hostname, port=parts.port,
            username=username, password=password,
            sni=sni, insecure=insecure, obfs=obfs,
            obfs_password=obfs_password,
            extra={**q, **({"dialer": dialer} if dialer else {})})

    return ProxyInfo(type=scheme, host=parts.hostname, port=parts.port,
                     username=username, password=password)


def _truthy(value: str | None) -> bool:
    """查询参数真值判定：1/true/yes/on（大小写不敏感）为真。"""
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def _b64decode_json(text: str) -> dict:
    """Base64 → JSON dict（vmess 标准形式；容忍缺 padding）。"""
    t = text.strip().replace("-", "+").replace("_", "/")
    t += "=" * (-len(t) % 4)
    return _json.loads(base64.b64decode(t).decode("utf-8", errors="replace"))


def _parse_vmess(line: str, text: str, q: dict,
                 sni: str | None, insecure: bool) -> ProxyInfo:
    """vmess://base64(json)：解析 v2rayN 标准 JSON 载荷。

    字段：add(host) port id(uuid) aid(alterId) net(ws/tcp/grpc...)
          type(none/http) host path tls sni ps(名称)
    """
    payload = text.split("://", 1)[1]
    try:
        d = _b64decode_json(payload)
    except (ValueError, binascii.Error) as e:
        raise ProxyParseError(f"vmess 载荷解析失败: {line}") from e
    host = d.get("add") or ""
    try:
        port = int(d.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    if not host or not port:
        raise ProxyParseError(f"vmess 缺少主机或端口: {line}")
    extra = {
        "uuid": d.get("id") or "",
        "alterId": d.get("aid") or "0",
        "cipher": d.get("scy") or "auto",
        "network": d.get("net") or "tcp",
        "header_type": d.get("type") or "none",
        "ws_host": d.get("host") or "",
        "path": d.get("path") or "",
    }
    tls = (d.get("tls") or "").lower()
    if tls and tls != "none":
        extra["tls"] = True
        if d.get("sni"):
            sni = d["sni"]
    if d.get("ps"):
        extra["ps"] = d["ps"]
    return ProxyInfo(
        type="vmess", host=host, port=port,
        username=None, password=d.get("id") or None,
        sni=sni, insecure=insecure or _truthy(d.get("allowInsecure")),
        extra=extra)


def _parse_ss(line: str, text: str, q: dict, sni: str | None,
              insecure: bool, dialer: str | None) -> ProxyInfo:
    """ss:// 两种编码：
    1) ss://base64(method:password)@host:port#name
    2) ss://base64(method:password@host:port)#name（SIP002 旧式）
    """
    rest = text.split("://", 1)[1]
    # 去掉 fragment（#name）
    if "#" in rest:
        rest, _, frag = rest.partition("#")
        frag = unquote(frag)
    else:
        frag = ""
    method = password = host = None
    port = 0
    if "@" in rest:
        # 形式 1：base64(userinfo)@host:port
        userinfo, _, hostport = rest.rpartition("@")
        try:
            decoded = base64.b64decode(
                userinfo + "=" * (-len(userinfo) % 4)).decode("utf-8")
        except (binascii.Error, ValueError) as e:
            raise ProxyParseError(f"ss 载荷解析失败: {line}") from e
        if ":" not in decoded:
            raise ProxyParseError(f"ss 载荷缺少 method:password: {line}")
        method, _, password = decoded.partition(":")
        hp = hostport.split(":")
        if len(hp) != 2:
            raise ProxyParseError(f"ss 缺少端口: {line}")
        host = hp[0]
        try:
            port = int(hp[1])
        except ValueError as e:
            raise ProxyParseError(f"ss 端口非法: {line}") from e
    else:
        # 形式 2：整体 base64(method:password@host:port)
        try:
            decoded = base64.b64decode(
                rest + "=" * (-len(rest) % 4)).decode("utf-8")
        except (binascii.Error, ValueError) as e:
            raise ProxyParseError(f"ss 载荷解析失败: {line}") from e
        if "@" not in decoded:
            raise ProxyParseError(f"ss 载荷格式非法: {line}")
        userinfo, _, hostport = decoded.rpartition("@")
        if ":" not in userinfo:
            raise ProxyParseError(f"ss 载荷缺少 method:password: {line}")
        method, _, password = userinfo.partition(":")
        hp = hostport.split(":")
        if len(hp) != 2:
            raise ProxyParseError(f"ss 缺少端口: {line}")
        host = hp[0]
        try:
            port = int(hp[1])
        except ValueError as e:
            raise ProxyParseError(f"ss 端口非法: {line}") from e
    extra = {"cipher": method or "aes-256-gcm"}
    if frag:
        extra["ps"] = frag
    if dialer:
        extra["dialer"] = dialer
    return ProxyInfo(type="ss", host=host or "", port=port,
                     username=None, password=password, sni=sni,
                     insecure=insecure, extra=extra)


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
    """国家码 → locale 建议（用于档案与代理地理对齐）。

    覆盖 60+ 国家/地区（借鉴 GeekezBrowser 的语言库广度）。
    """
    mapping = {
        # 英语区
        "US": "en-US", "GB": "en-GB", "CA": "en-CA", "AU": "en-AU",
        "NZ": "en-NZ", "IE": "en-IE", "ZA": "en-ZA", "SG": "en-SG",
        "IN": "en-IN", "PH": "en-PH", "MY": "en-MY", "HK": "zh-HK",
        # 欧洲
        "DE": "de-DE", "AT": "de-AT", "CH": "de-CH", "FR": "fr-FR",
        "BE": "nl-BE", "NL": "nl-NL", "IT": "it-IT", "ES": "es-ES",
        "PT": "pt-PT", "PL": "pl-PL", "CZ": "cs-CZ", "SE": "sv-SE",
        "NO": "nb-NO", "DK": "da-DK", "FI": "fi-FI", "GR": "el-GR",
        "HU": "hu-HU", "RO": "ro-RO", "BG": "bg-BG", "HR": "hr-HR",
        "SK": "sk-SK", "SI": "sl-SI", "RS": "sr-RS", "UA": "uk-UA",
        "RU": "ru-RU", "TR": "tr-TR", "IS": "is-IS", "EE": "et-EE",
        "LV": "lv-LV", "LT": "lt-LT",
        # 亚太
        "JP": "ja-JP", "KR": "ko-KR", "CN": "zh-CN", "TW": "zh-TW",
        "TH": "th-TH", "VN": "vi-VN", "ID": "id-ID", "KH": "km-KH",
        "MM": "my-MM", "BD": "bn-BD", "PK": "ur-PK", "LK": "si-LK",
        "NP": "ne-NP", "KZ": "kk-KZ", "MN": "mn-MN",
        # 美洲
        "BR": "pt-BR", "MX": "es-MX", "AR": "es-AR", "CL": "es-CL",
        "CO": "es-CO", "PE": "es-PE", "UY": "es-UY",
        # 中东/非洲
        "AE": "ar-AE", "SA": "ar-SA", "EG": "ar-EG", "IL": "he-IL",
        "NG": "en-NG", "KE": "en-KE", "MA": "ar-MA", "GH": "en-GH",
    }
    return mapping.get((country_code or "").upper(), "en-US")
