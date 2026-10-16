"""Vault 单测：初始化 / 加解密往返 / 错误密码拒绝 / 篡改检测。"""

import pytest

from fabletech.config import set_data_dir_override
from fabletech.core.crypto import MAGIC, NONCE_LEN, Vault, VaultError

PW = "Master#Passw0rd"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    set_data_dir_override(tmp_path)
    yield tmp_path / "fabletech.db"
    from fabletech.config import clear_data_dir_override
    clear_data_dir_override()


def test_initialize_and_is_initialized(db):
    assert Vault.is_initialized(db) is False
    v = Vault.initialize(db, PW)
    assert Vault.is_initialized(db) is True
    assert isinstance(v, Vault)


def test_weak_password_rejected(db):
    with pytest.raises(VaultError):
        Vault.initialize(db, "short")


def test_encrypt_decrypt_roundtrip(db):
    v = Vault.initialize(db, PW)
    for text in ("", "hello", "中文密码 passw0rd!", "x" * 5000):
        blob = v.encrypt(text)
        assert blob[:4] == MAGIC
        assert v.decrypt(blob) == text.encode("utf-8")


def test_blob_format_and_random_nonce(db):
    v = Vault.initialize(db, PW)
    b1 = v.encrypt("same")
    b2 = v.encrypt("same")
    assert b1 != b2  # 随机 nonce
    assert b1[4:4 + NONCE_LEN] != b2[4:4 + NONCE_LEN]


def test_wrong_password_rejected(db):
    Vault.initialize(db, PW)
    with pytest.raises(VaultError):
        Vault.unlock(db, "Wrong#Password1")


def test_unlock_success(db):
    Vault.initialize(db, PW)
    v = Vault.unlock(db, PW)
    assert v.decrypt_str(v.encrypt_str("secret")) == "secret"


def test_tamper_detection(db):
    v = Vault.initialize(db, PW)
    blob = bytearray(v.encrypt("sensitive data"))
    blob[-1] ^= 0xFF  # 翻转密文末位 → GCM tag 失效
    with pytest.raises(VaultError):
        v.decrypt(bytes(blob))


def test_tamper_detection_bad_magic(db):
    v = Vault.initialize(db, PW)
    blob = bytearray(v.encrypt("x"))
    blob[0] ^= 0xFF
    with pytest.raises(VaultError):
        v.decrypt(bytes(blob))


def test_hex_str_helpers(db):
    v = Vault.initialize(db, PW)
    h = v.encrypt_str("用户名:密码")
    assert isinstance(h, str)
    assert v.decrypt_str(h) == "用户名:密码"
