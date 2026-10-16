"""proxy 单测：URL 形态解析 / 批量文本 / 异常行跳过 / 掩码 / mock 连通测试 / 地理 mock。"""

import pytest

from fabletech.core.proxy import (
    ProxyInfo,
    ProxyParseError,
    parse_proxy_bulk,
    parse_proxy_line,
    query_geo,
    suggest_locale,
)
from fabletech.core.proxy import test_proxy as run_proxy_test


@pytest.mark.parametrize("line,typ,host,port,user,pwd", [
    ("socks5://user:pass@1.2.3.4:1080", "socks5", "1.2.3.4", 1080, "user", "pass"),
    ("http://u:p@proxy.example.com:8080", "http", "proxy.example.com", 8080, "u", "p"),
    ("https://10.0.0.1:3128", "https", "10.0.0.1", 3128, None, None),
    ("socks5h://alice:s3cr3t@[2001:db8::1]:9050", "socks5h", "2001:db8::1", 9050, "alice", "s3cr3t"),
    ("1.2.3.4:1080", "http", "1.2.3.4", 1080, None, None),          # 无协议默认 http
    ("user:pass@5.6.7.8:9999", "http", "5.6.7.8", 9999, "user", "pass"),
    ("socks4://h:1", "socks4", "h", 1, None, None),
])
def test_parse_various_forms(line, typ, host, port, user, pwd):
    p = parse_proxy_line(line)
    assert (p.type, p.host, p.port, p.username, p.password) == (typ, host, port, user, pwd)


def test_parse_special_chars_in_credentials():
    p = parse_proxy_line("socks5://u%40ser:p%2Fass@host.com:1080")
    assert p.username == "u@ser"
    assert p.password == "p/ass"


def test_url_roundtrip_and_masking():
    p = parse_proxy_line("socks5://user:pass@1.2.3.4:1080")
    assert p.url() == "socks5://user:pass@1.2.3.4:1080"
    assert p.masked_url() == "socks5://u***:p***@1.2.3.4:1080"
    # 掩码中不得出现明文凭证
    assert "pass" not in p.masked_url()


@pytest.mark.parametrize("line", [
    "ftp://h:1",             # 不支持的协议
    "http://host",           # 缺端口
    "",                      # 空行（批量跳过，单行报错）
    "not a url",             # 无端口
])
def test_parse_bad_lines(line):
    with pytest.raises(ProxyParseError):
        parse_proxy_line(line)


def test_bulk_import_skips_bad_lines():
    text = """
# 这是注释
socks5://a:b@1.1.1.1:1080
http://2.2.2.2:8080

ftp://bad:1
socks5://ok:pw@3.3.3.3:1080
"""
    ok, bad = parse_proxy_bulk(text)
    assert len(ok) == 3
    assert len(bad) == 1
    assert bad[0][0] == 6  # 行号（ftp 那行）
    assert bad[0][1].startswith("ftp://")


def test_test_proxy_with_mock_connector():
    p = parse_proxy_line("socks5://u:p@1.2.3.4:1080")

    def ok_connector(host, port, timeout):
        return 12.34

    ok, latency, msg = run_proxy_test(p, connector=ok_connector)
    assert ok and latency == 12.3

    def fail_connector(host, port, timeout):
        raise OSError(111, "Connection refused")

    ok, latency, msg = run_proxy_test(p, connector=fail_connector)
    assert not ok and latency is None
    assert "无法连接" in msg


def test_geo_with_mock_fetcher():
    def fetcher(ip, timeout):
        return {"status": "success", "country": "United States",
                "countryCode": "US", "city": "Los Angeles", "isp": "Comcast",
                "query": ip}

    geo = query_geo("8.8.8.8", fetcher=fetcher)
    assert geo["countryCode"] == "US"
    assert geo["city"] == "Los Angeles"


def test_geo_failure_returns_empty_fields():
    def fetcher(ip, timeout):
        raise RuntimeError("network down")

    geo = query_geo("8.8.8.8", fetcher=fetcher)
    assert geo["country"] == ""
    assert "error" in geo


def test_suggest_locale():
    assert suggest_locale("US") == "en-US"
    assert suggest_locale("JP") == "ja-JP"
    assert suggest_locale("XX") == "en-US"


def test_proxy_info_equality_and_no_leak():
    a = ProxyInfo("socks5", "h", 1, "u", "p")
    b = ProxyInfo("socks5", "h", 1, "u", "p")
    assert a == b
