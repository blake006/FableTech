"""hy2/anytls 解析与中转配置单测。

覆盖：
- hy2:// / hysteria2:// / anytls:// URI 解析（auth/sni/insecure/obfs 参数）
- 类型别名归一化
- 扩展参数 dict 往返（存储序列化）
- mihomo 配置生成（YAML 结构）
- SOCKS5 最小客户端（mock socket）
"""

import json

import pytest

from fabletech.core.proxy import (
    ProxyInfo,
    ProxyParseError,
    parse_proxy_bulk,
    parse_proxy_line,
    socks5_http_get,
)
from fabletech.core.relay import build_config


# ---------- URI 解析 ----------

def test_parse_hy2_full():
    p = parse_proxy_line(
        "hy2://mypassword@hy2.example.com:443/?sni=real.example.com"
        "&insecure=1&obfs=salamander&obfs-password=xyz123")
    assert p.type == "hysteria2"          # hy2 别名归一化
    assert p.host == "hy2.example.com"
    assert p.port == 443
    assert p.password == "mypassword"
    assert p.sni == "real.example.com"
    assert p.insecure is True
    assert p.obfs == "salamander"
    assert p.obfs_password == "xyz123"


def test_parse_hysteria2_alias():
    p = parse_proxy_line("hysteria2://auth@h.example.com:8443?sni=h.example.com")
    assert p.type == "hysteria2"
    assert p.password == "auth"
    assert p.sni == "h.example.com"
    assert p.insecure is False


def test_parse_anytls():
    p = parse_proxy_line("anytls://secretpw@atl.example.com:443"
                         "?sni=atl.example.com&insecure=1")
    assert p.type == "anytls"
    assert p.password == "secretpw"
    assert p.sni == "atl.example.com"
    assert p.insecure is True


def test_parse_anytls_alias():
    p = parse_proxy_line("anytls://pw@atl.example.com:8443")
    assert p.type == "anytls"
    assert p.password == "pw"
    assert p.sni is None


def test_parse_hy2_obfs_param_alias():
    # obfsParam 是部分客户端的混淆密码参数名
    p = parse_proxy_line("hy2://a@h.com:443?obfs=salamander&obfsParam=abc")
    assert p.obfs == "salamander"
    assert p.obfs_password == "abc"


def test_parse_insecure_false_variants():
    p = parse_proxy_line("hy2://a@h.com:443?insecure=0")
    assert p.insecure is False
    p2 = parse_proxy_line("hy2://a@h.com:443")
    assert p2.insecure is False


def test_parse_hy2_no_auth():
    # hy2 无 userinfo（部分服务器 auth 在参数里）
    p = parse_proxy_line("hy2://h.com:443")
    assert p.type == "hysteria2"
    assert p.password is None
    assert p.username is None


def test_extra_dict_roundtrip():
    p = parse_proxy_line(
        "hy2://mypw@h.com:443/?sni=r.com&insecure=1"
        "&obfs=salamander&obfs-password=xyz")
    d = p.extra_to_dict()
    assert d["sni"] == "r.com"
    assert d["insecure"] is True
    assert d["obfs"] == "salamander"
    assert d["obfs_password"] == "xyz"
    # 序列化 → 反序列化往返
    p2 = ProxyInfo.from_extra("hysteria2", "h.com", 443, None, "mypw",
                              json.loads(json.dumps(d)))
    assert p2.sni == "r.com"
    assert p2.insecure is True
    assert p2.obfs == "salamander"
    assert p2.obfs_password == "xyz"
    assert p2.password == "mypw"


def test_bulk_import_with_relay_types():
    text = (
        "# 混合导入\n"
        "socks5://u:p@1.2.3.4:1080\n"
        "hy2://pw@h1.com:443?sni=r1.com&insecure=1\n"
        "anytls://pw@h2.com:8443?sni=r2.com\n"
    )
    ok, bad = parse_proxy_bulk(text)
    assert len(ok) == 3
    assert not bad
    types = {p.type for p in ok}
    assert types == {"socks5", "hysteria2", "anytls"}


def test_relay_url_masking():
    p = parse_proxy_line("hy2://mypw@h.com:443?sni=r.com")
    masked = p.masked_url()
    assert "mypw" not in masked
    assert "h.com" in masked


def test_bad_relay_uri():
    with pytest.raises(ProxyParseError):
        parse_proxy_line("hy2://host")  # 缺端口


# ---------- mihomo 配置生成 ----------

def test_build_config_hysteria2():
    p = parse_proxy_line(
        "hy2://mypw@h.com:443/?sni=r.com&insecure=1"
        "&obfs=salamander&obfs-password=xyz")
    cfg = build_config(p, 7890)
    assert "mixed-port: 7890" in cfg
    assert 'type: "hysteria2"' in cfg
    assert "h.com" in cfg
    assert "salamander" in cfg
    assert "xyz" in cfg
    assert "r.com" in cfg
    assert "skip-cert-verify" in cfg
    assert "h3" in cfg


def test_build_config_anytls():
    p = parse_proxy_line("anytls://pw@h.com:8443?sni=r.com&insecure=1")
    cfg = build_config(p, 7891)
    assert "mixed-port: 7891" in cfg
    assert 'type: "anytls"' in cfg
    assert "pw" in cfg
    assert "r.com" in cfg
    # anytls 不应出现 hysteria2 专属字段
    assert "obfs" not in cfg
    assert "h3" not in cfg


def test_build_config_rejects_plain_type():
    p = ProxyInfo("socks5", "h", 1, None, None)
    with pytest.raises(Exception):
        build_config(p, 7890)


# ---------- SOCKS5 最小客户端（mock） ----------

class _ScriptedSock:
    """按预设脚本依次回包的假 socket（验证 SOCKS5 协议时序）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = b""

    def sendall(self, data):
        self.sent += data

    def settimeout(self, t):
        pass

    def recv(self, n):
        return self.responses.pop(0) if self.responses else b""

    def close(self):
        pass


def test_socks5_http_get(monkeypatch):
    import socket

    fake = _ScriptedSock([
        b"\x05\x00",                    # 握手响应（无认证）
        b"\x05\x00\x00\x03",            # CONNECT 成功（域名形式）
        b"\x0b",                        # 域名长度 11
        b"api.example",                 # 域名（11 字节）
        b"\x00\x50",                    # 端口 80
        b"HTTP/1.1 200 OK\r\n\r\n",     # HTTP 响应
        b'{"status":"success"}',
        b"",                            # 连接关闭
    ])
    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: fake)
    raw = socks5_http_get("127.0.0.1", 7890, "http://api.example/json/")
    assert b"200 OK" in raw
    assert b"success" in raw
    # 握手报文：版本5 + 1 个方法 + 无认证
    assert fake.sent[:3] == b"\x05\x01\x00"
    # CONNECT 报文：\x05\x01\x00 + ATYP=3（域名）
    assert fake.sent[3:6] == b"\x05\x01\x00"
    assert fake.sent[6] == 0x03
    assert b"api.example" in fake.sent
