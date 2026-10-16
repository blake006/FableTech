"""Cookie 仓：Netscape 格式解析/导出、JSON 互转、经 Vault 加密封存/读取。

存储位置：config.paths().cookies/<profile_id>.enc（AES-256-GCM 密文）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import paths, ensure_dirs
from .crypto import Vault

_NETSCAPE_HEADER = "# Netscape HTTP Cookie File"

# Netscape 行：domain flag path secure expiry name value
_NETSCAPE_LINE = re.compile(
    r"^(?P<domain>[^\s]+)\s+"
    r"(?P<flag>TRUE|FALSE)\s+"
    r"(?P<path>[^\s]+)\s+"
    r"(?P<secure>TRUE|FALSE)\s+"
    r"(?P<expiry>\d+)\s+"
    r"(?P<name>[^\s]+)\s+"
    r"(?P<value>.*)$"
)


class CookieFormatError(ValueError):
    """Cookie 格式解析失败。"""


def parse_netscape(text: str) -> list[dict]:
    """解析 Netscape cookies.txt 格式（注释/空行跳过，坏行报错带行号）。"""
    cookies: list[dict] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        # 先剥 #HttpOnly_ 前缀（它不是普通注释）
        httponly = False
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
            httponly = True
        elif line.startswith("#"):
            continue
        m = _NETSCAPE_LINE.match(line)
        if not m:
            raise CookieFormatError(f"第 {lineno} 行不是合法的 Netscape Cookie 行")
        c = {
            "domain": m.group("domain"),
            "path": m.group("path"),
            "secure": m.group("secure") == "TRUE",
            "expires": int(m.group("expiry")),
            "name": m.group("name"),
            "value": m.group("value"),
            "httpOnly": httponly,
        }
        if m.group("flag") == "TRUE" and not c["domain"].startswith("."):
            c["domain"] = "." + c["domain"]
        cookies.append(c)
    return cookies


def to_netscape(cookies: list[dict]) -> str:
    """导出 Netscape 格式文本。"""
    lines = [_NETSCAPE_HEADER, ""]
    for c in cookies:
        prefix = "#HttpOnly_" if c.get("httpOnly") else ""
        domain = c.get("domain", "")
        include_sub = domain.startswith(".")
        flag = "TRUE" if include_sub else "FALSE"
        expires = int(c.get("expires") or 0)
        lines.append(
            f"{prefix}{domain}\t{flag}\t{c.get('path', '/')}\t"
            f"{'TRUE' if c.get('secure') else 'FALSE'}\t{expires}\t"
            f"{c.get('name', '')}\t{c.get('value', '')}"
        )
    return "\n".join(lines) + "\n"


def json_to_netscape(data: Any) -> list[dict]:
    """JSON cookie 数组 → 统一 cookie dict 列表。

    接受两种来源：Playwright storage_state 的 cookies，或浏览器扩展导出的 JSON。
    """
    if isinstance(data, dict):
        # storage_state 结构 {"cookies": [...], "origins": [...]}
        items = data.get("cookies")
        if items is None:
            raise CookieFormatError("JSON 中缺少 cookies 字段")
    elif isinstance(data, list):
        items = data
    else:
        raise CookieFormatError("JSON 顶层必须是数组或含 cookies 字段的对象")
    out: list[dict] = []
    for c in items:
        if not isinstance(c, dict) or "name" not in c or "value" not in c:
            raise CookieFormatError("JSON cookie 条目缺少 name/value")
        expires = c.get("expires") or c.get("expirationDate") or 0
        try:
            expires = int(float(expires))
        except (TypeError, ValueError):
            expires = 0
        out.append({
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "secure": bool(c.get("secure", False)),
            "expires": expires,
            "name": str(c["name"]),
            "value": str(c["value"]),
            "httpOnly": bool(c.get("httpOnly", False)),
        })
    return out


def netscape_to_json(cookies: list[dict]) -> list[dict]:
    """统一 cookie dict → 浏览器扩展风格 JSON 条目。"""
    return [{
        "domain": c["domain"],
        "path": c.get("path", "/"),
        "secure": c.get("secure", False),
        "expirationDate": c.get("expires", 0),
        "name": c["name"],
        "value": c["value"],
        "httpOnly": c.get("httpOnly", False),
    } for c in cookies]


class CookieVault:
    """Cookie 加密仓：<data>/cookies/<profile_id>.enc。"""

    def __init__(self, vault: Vault) -> None:
        self._vault = vault
        ensure_dirs(paths())

    def _path(self, profile_id: str) -> Path:
        # 路径安全：profile_id 限定为 12 位 hex，防路径穿越
        if not re.fullmatch(r"[0-9a-f]{1,32}", profile_id):
            raise ValueError(f"非法档案 ID: {profile_id}")
        return paths().cookies / f"{profile_id}.enc"

    def save(self, profile_id: str, cookies: list[dict], engine: str = "") -> Path:
        payload = {
            "engine": engine,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cookies": cookies,
        }
        blob = self._vault.encrypt(json.dumps(payload, ensure_ascii=False))
        p = self._path(profile_id)
        p.write_bytes(blob)
        return p

    def load(self, profile_id: str) -> dict | None:
        """返回 {engine, saved_at, cookies}；不存在则 None。"""
        p = self._path(profile_id)
        if not p.exists():
            return None
        plain = self._vault.decrypt(p.read_bytes()).decode("utf-8")
        return json.loads(plain)

    def exists(self, profile_id: str) -> bool:
        return self._path(profile_id).exists()

    def delete(self, profile_id: str) -> bool:
        p = self._path(profile_id)
        if p.exists():
            p.unlink()
            return True
        return False

    def import_netscape(self, profile_id: str, text: str, engine: str = "") -> int:
        """从 Netscape 文本导入并加密封存，返回 cookie 条数。"""
        cookies = parse_netscape(text)
        self.save(profile_id, cookies, engine)
        return len(cookies)

    def import_json(self, profile_id: str, data: Any, engine: str = "") -> int:
        """从 JSON（数组或 storage_state）导入并加密封存。"""
        cookies = json_to_netscape(data)
        self.save(profile_id, cookies, engine)
        return len(cookies)

    def export_netscape(self, profile_id: str) -> str | None:
        loaded = self.load(profile_id)
        if loaded is None:
            return None
        return to_netscape(loaded["cookies"])

    def export_json(self, profile_id: str) -> list[dict] | None:
        loaded = self.load(profile_id)
        if loaded is None:
            return None
        return netscape_to_json(loaded["cookies"])
