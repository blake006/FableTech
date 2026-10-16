"""Vault：主密码 → scrypt → AES-256-GCM 加密仓。

格式：magic(4B="FTV1") | nonce(12B) | ciphertext+GCM tag
校验值：加密固定串 "FableTech-Vault-Check" 存 settings 表，用于验证主密码。
密钥仅内存持有，永不落盘。
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"FTV1"
NONCE_LEN = 12
KEY_LEN = 32
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
CHECK_PLAINTEXT = "FableTech-Vault-Check"


class VaultError(Exception):
    """Vault 通用错误（未初始化 / 密码错误 / 数据损坏）。"""


@dataclass
class VaultMeta:
    salt: bytes
    check_value: bytes


class Vault:
    """AES-256-GCM 加密仓。db_path 即 SQLite 主库（settings 表存校验值）。"""

    def __init__(self, db_path: Path, meta: VaultMeta, key: bytes) -> None:
        self._db_path = Path(db_path)
        self._meta = meta
        self._aes = AESGCM(key)

    # ---------- 初始化与解锁 ----------

    @staticmethod
    def is_initialized(db_path: Path) -> bool:
        """库存在且 settings 表已有 vault_check 即为已初始化。"""
        db_path = Path(db_path)
        if not db_path.exists():
            return False
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT value FROM settings WHERE key='vault_check'"
                ).fetchone()
                return row is not None
        except sqlite3.Error:
            return False

    @classmethod
    def initialize(cls, db_path: Path, master_password: str) -> "Vault":
        if len(master_password) < 8:
            raise VaultError("主密码至少 8 位")
        salt = os.urandom(32)
        key = _derive(master_password, salt)
        aes = AESGCM(key)
        # 校验值格式：nonce(12B) + ct+tag，解锁时 decrypt(check[NONCE_LEN:], ...)
        nonce = os.urandom(NONCE_LEN)
        check = nonce + aes.encrypt(nonce, CHECK_PLAINTEXT.encode("utf-8"), b"")
        meta = VaultMeta(salt=salt, check_value=check)
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES ('vault_salt', ?)",
                (salt.hex(),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES ('vault_check', ?)",
                (check.hex(),),
            )
        return cls(db_path, meta, key)

    @classmethod
    def unlock(cls, db_path: Path, master_password: str) -> "Vault":
        """用主密码解锁；密码错误抛 VaultError。"""
        db_path = Path(db_path)
        if not cls.is_initialized(db_path):
            raise VaultError("Vault 尚未初始化，请先设置主密码")
        with sqlite3.connect(db_path) as conn:
            salt_hex, check_hex = conn.execute(
                "SELECT (SELECT value FROM settings WHERE key='vault_salt'),"
                "       (SELECT value FROM settings WHERE key='vault_check')"
            ).fetchone()
        salt = bytes.fromhex(salt_hex)
        check = bytes.fromhex(check_hex)
        key = _derive(master_password, salt)
        try:
            plain = AESGCM(key).decrypt(
                check[:NONCE_LEN], check[NONCE_LEN:], b"",
            )
        except (InvalidTag, ValueError) as e:
            raise VaultError("主密码错误") from e
        if plain != CHECK_PLAINTEXT.encode("utf-8"):
            raise VaultError("主密码错误")
        return cls(db_path, VaultMeta(salt=salt, check_value=check), key)

    # ---------- 加解密 ----------

    def encrypt(self, plaintext: str | bytes) -> bytes:
        data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
        nonce = os.urandom(NONCE_LEN)
        ct = self._aes.encrypt(nonce, data, b"")
        return MAGIC + nonce + ct

    def decrypt(self, blob: bytes) -> bytes:
        if not isinstance(blob, (bytes, bytearray)) or len(blob) < 4 + NONCE_LEN + 16:
            raise VaultError("密文格式非法（长度不足）")
        if bytes(blob[:4]) != MAGIC:
            raise VaultError("密文格式非法（magic 不匹配）")
        nonce = bytes(blob[4:4 + NONCE_LEN])
        ct = bytes(blob[4 + NONCE_LEN:])
        try:
            return self._aes.decrypt(nonce, ct, b"")
        except InvalidTag as e:
            raise VaultError("解密失败：数据被篡改或密钥不匹配") from e

    def encrypt_str(self, plaintext: str) -> str:
        """加密并返回 hex 字符串（便于存 TEXT 字段）。"""
        return self.encrypt(plaintext).hex()

    def decrypt_str(self, hex_blob: str) -> str:
        return self.decrypt(bytes.fromhex(hex_blob)).decode("utf-8")


def _derive(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=KEY_LEN, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(
        password.encode("utf-8")
    )
