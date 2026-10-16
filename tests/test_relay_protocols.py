"""hy2/anytls 解析与中转配置单测。

覆盖：
- hy2:// / hysteria2:// / anytls:// URI 解析（auth/sni/insecure/obfs 参数）
- 类型别名归一化
- 扩展参数 dict 往返（存储序列化）
- mihomo 配置生成（YAML 结构）
- SOCKS5 最小客户端（mock socket）
"""

import json as _json

import pytest

from fabletech.core.proxy import (
    ProxyInfo,
    ProxyParseError,
    parse_proxy_bulk,
    parse_proxy_line,
    socks5_http_get,
    suggest_locale,
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
                              _json.loads(_json.dumps(d)))
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


# ---------- 机场协议族（vmess/vless/trojan/ss） ----------

import base64 as _b64


def test_parse_vmess_standard():
    # v2rayN 标准 vmess 链接：base64(json)
    payload = _b64.b64encode(_json.dumps({
        "v": "2", "ps": "测试节点", "add": "vm.example.com", "port": "443",
        "id": "b831381d-6324-4d53-ad4f-8cda48b30811", "aid": "0",
        "scy": "auto", "net": "ws", "type": "none", "host": "cdn.example.com",
        "path": "/ray", "tls": "tls", "sni": "vm.example.com",
    }, ensure_ascii=False).encode()).decode()
    p = parse_proxy_line(f"vmess://{payload}")
    assert p.type == "vmess"
    assert p.host == "vm.example.com" and p.port == 443
    assert p.password == "b831381d-6324-4d53-ad4f-8cda48b30811"
    assert p.extra["network"] == "ws"
    assert p.extra["path"] == "/ray"
    assert p.extra["ws_host"] == "cdn.example.com"
    assert p.extra["tls"] is True
    assert p.sni == "vm.example.com"


def test_parse_vmess_bad_payload():
    with pytest.raises(ProxyParseError):
        parse_proxy_line("vmess://not-valid-base64!!!")


def test_parse_vless_reality():
    p = parse_proxy_line(
        "vless://uuid-here@vl.example.com:443?encryption=none&security=reality"
        "&sni=www.microsoft.com&fp=chrome&pbk=PubKey123&sid=ab12&type=tcp#节点1")
    assert p.type == "vless"
    assert p.host == "vl.example.com" and p.port == 443
    assert p.password == "uuid-here"
    assert p.sni == "www.microsoft.com"
    assert p.extra["security"] == "reality"
    assert p.extra["pbk"] == "PubKey123"
    assert p.extra["fp"] == "chrome"


def test_parse_trojan():
    p = parse_proxy_line(
        "trojan://pass123@tj.example.com:8443?sni=tj.example.com&type=ws"
        "&path=/tr&host=tj.example.com")
    assert p.type == "trojan"
    assert p.password == "pass123"
    assert p.sni == "tj.example.com"
    assert p.extra["type"] == "ws"
    assert p.extra["path"] == "/tr"


def test_parse_ss_form1():
    userinfo = _b64.b64encode(b"aes-256-gcm:mypassword").decode()
    p = parse_proxy_line(f"ss://{userinfo}@ss.example.com:8388#节点A")
    assert p.type == "ss"
    assert p.host == "ss.example.com" and p.port == 8388
    assert p.password == "mypassword"
    assert p.extra["cipher"] == "aes-256-gcm"
    assert p.extra["ps"] == "节点A"


def test_parse_ss_form2():
    whole = _b64.b64encode(b"chacha20-ietf-poly1305:pw2@ss2.example.com:9443").decode()
    p = parse_proxy_line(f"ss://{whole}")
    assert p.type == "ss"
    assert p.host == "ss2.example.com" and p.port == 9443
    assert p.password == "pw2"
    assert p.extra["cipher"] == "chacha20-ietf-poly1305"


def test_dialer_param():
    p = parse_proxy_line("hy2://pw@h.com:443?dialer=abc123")
    assert p.extra.get("dialer") == "abc123"
    p2 = parse_proxy_line("ss://" + _b64.b64encode(b"aes-256-gcm:pw@h.com:1").decode()
                          + "?dialer=xyz")
    assert p2.extra.get("dialer") == "xyz"


# ---------- mihomo 配置：新协议 ----------

def test_build_config_vmess():
    payload = _b64.b64encode(_json.dumps({
        "add": "vm.com", "port": "443", "id": "uuid-1", "aid": "0",
        "net": "ws", "path": "/ray", "host": "cdn.com", "tls": "tls",
    }).encode()).decode()
    p = parse_proxy_line(f"vmess://{payload}")
    cfg = build_config(p, 7890)
    assert 'type: "vmess"' in cfg
    assert "uuid-1" in cfg
    assert "/ray" in cfg
    assert "cdn.com" in cfg


def test_build_config_vless_reality():
    p = parse_proxy_line(
        "vless://uuid-1@vl.com:443?security=reality&sni=www.apple.com"
        "&fp=chrome&pbk=KEY1&sid=ab&type=tcp")
    cfg = build_config(p, 7890)
    assert 'type: "vless"' in cfg
    assert "reality-opts" in cfg
    assert "KEY1" in cfg
    assert "www.apple.com" in cfg


def test_build_config_trojan_and_ss():
    p1 = parse_proxy_line("trojan://pw@tj.com:443?sni=tj.com&insecure=1")
    cfg1 = build_config(p1, 7890)
    assert 'type: "trojan"' in cfg1
    assert "skip-cert-verify" in cfg1
    p2 = parse_proxy_line("ss://" + _b64.b64encode(b"aes-256-gcm:pw@ss.com:80").decode())
    cfg2 = build_config(p2, 7891)
    assert 'type: "ss"' in cfg2
    assert "aes-256-gcm" in cfg2


def test_build_config_dialer_chain():
    """预代理链：dialer 出站 + dialer-proxy 引用。"""
    upstream = parse_proxy_line("hy2://pw@h.com:443?sni=r.com")
    dialer = parse_proxy_line(
        "ss://" + _b64.b64encode(b"aes-256-gcm:dp@d.com:80").decode())
    cfg = build_config(upstream, 7890, dialer=dialer)
    assert "dialer-proxy" in cfg
    assert 'name: "dialer"' in cfg
    assert 'name: "upstream"' in cfg
    assert "d.com" in cfg and "h.com" in cfg


# ---------- 语言库 ----------

def test_suggest_locale_extended():
    assert suggest_locale("JP") == "ja-JP"
    assert suggest_locale("DE") == "de-DE"
    assert suggest_locale("BR") == "pt-BR"
    assert suggest_locale("TH") == "th-TH"
    assert suggest_locale("AE") == "ar-AE"
    assert suggest_locale("XX") == "en-US"   # 未知回退
    assert suggest_locale("") == "en-US"
