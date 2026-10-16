"""cookies 单测：Netscape 往返保真 / JSON 互转 / 加密落盘读回 / 路径穿越防护。"""

import json

import pytest

from fabletech.config import set_data_dir_override
from fabletech.core.cookies import (
    CookieFormatError,
    CookieVault,
    json_to_netscape,
    netscape_to_json,
    parse_netscape,
    to_netscape,
)
from fabletech.core.crypto import Vault, VaultError

PW = "Master#Passw0rd"

SAMPLE_NETSCAPE = """# Netscape HTTP Cookie File
# 注释应被跳过
.example.com\tTRUE\t/\tFALSE\t0\tsid\tabc123
.example.com\tFALSE\t/\tTRUE\t1893456000\tpref\tdark
#HttpOnly_.example.com\tFALSE\t/api\tTRUE\t1893456000\ttoken\txyz789
"""


@pytest.fixture()
def vault(tmp_path):
    set_data_dir_override(tmp_path)
    v = Vault.initialize(tmp_path / "fabletech.db", PW)
    yield CookieVault(v)
    from fabletech.config import clear_data_dir_override
    clear_data_dir_override()


def test_parse_netscape_skips_comments(vault):
    cookies = parse_netscape(SAMPLE_NETSCAPE)
    assert len(cookies) == 3
    assert cookies[0] == {
        "domain": ".example.com", "path": "/", "secure": False,
        "expires": 0, "name": "sid", "value": "abc123", "httpOnly": False,
    }
    assert cookies[2]["httpOnly"] is True
    assert cookies[2]["secure"] is True


def test_netscape_roundtrip_fidelity(vault):
    cookies = parse_netscape(SAMPLE_NETSCAPE)
    again = parse_netscape(to_netscape(cookies))
    assert again == cookies


def test_bad_netscape_line_raises_with_lineno(vault):
    with pytest.raises(CookieFormatError, match="第 3 行"):
        parse_netscape("# Netscape HTTP Cookie File\n\nbad line here\n")


def test_json_to_netscape_playwright_style(vault):
    payload = {"cookies": [
        {"domain": ".x.com", "path": "/", "secure": True,
         "expirationDate": 1893456000, "name": "auth_token", "value": "t0k"},
        {"domain": "x.com", "name": "k", "value": "v"},
    ]}
    cookies = json_to_netscape(payload)
    assert len(cookies) == 2
    assert cookies[0]["expires"] == 1893456000
    assert cookies[0]["secure"] is True
    assert cookies[1]["expires"] == 0


def test_json_to_netscape_plain_list(vault):
    cookies = json_to_netscape([{"name": "n", "value": "v", "domain": ".d.com"}])
    assert len(cookies) == 1


def test_json_to_netscape_bad_payload(vault):
    with pytest.raises(CookieFormatError):
        json_to_netscape({"no_cookies": True})
    with pytest.raises(CookieFormatError):
        json_to_netscape([{"name": "only-name"}])


def test_netscape_to_json_roundtrip(vault):
    cookies = parse_netscape(SAMPLE_NETSCAPE)
    j = netscape_to_json(cookies)
    again = json_to_netscape(j)
    assert again == cookies


def test_vault_encrypted_save_load(vault):
    pid = "a" * 12
    cookies = parse_netscape(SAMPLE_NETSCAPE)
    path = vault.save(pid, cookies, engine="camoufox")
    assert path.exists()
    assert path.suffix == ".enc"
    # 落盘内容不得含明文 cookie 值
    raw = path.read_bytes()
    assert b"abc123" not in raw
    loaded = vault.load(pid)
    assert loaded["engine"] == "camoufox"
    assert loaded["cookies"] == cookies


def test_vault_import_export_netscape(vault):
    pid = "b" * 12
    n = vault.import_netscape(pid, SAMPLE_NETSCAPE)
    assert n == 3
    text = vault.export_netscape(pid)
    assert parse_netscape(text) == parse_netscape(SAMPLE_NETSCAPE)
    j = vault.export_json(pid)
    assert isinstance(j, list) and len(j) == 3


def test_vault_import_json(vault):
    pid = "c" * 12
    n = vault.import_json(pid, {"cookies": [
        {"domain": ".x.com", "name": "auth", "value": "v1"}]})
    assert n == 1
    assert vault.export_netscape(pid).find("auth") > 0


def test_vault_missing_returns_none(vault):
    assert vault.load("d" * 12) is None
    assert vault.export_netscape("d" * 12) is None
    assert vault.exists("d" * 12) is False


def test_vault_delete(vault):
    pid = "e" * 12
    vault.save(pid, [])
    assert vault.exists(pid)
    assert vault.delete(pid) is True
    assert vault.delete(pid) is False


def test_path_traversal_rejected(vault):
    for bad in ("../evil", "a/b", "UPPER", "a" * 33):
        with pytest.raises(ValueError):
            vault.save(bad, [])


def test_tampered_blob_detected(vault):
    pid = "f" * 12
    path = vault.save(pid, [])
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 0xFF
    path.write_bytes(bytes(raw))
    with pytest.raises(VaultError):
        vault.load(pid)
