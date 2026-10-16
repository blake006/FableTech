"""SQLite 存储层：profiles/proxies/cookies/tasks/logs/settings 六表 CRUD。

红线：所有 SQL 全部参数化，禁止字符串拼接；id 统一 uuid4().hex[:12]。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    engine TEXT NOT NULL,
    seed TEXT NOT NULL,
    platform_tags TEXT DEFAULT '',
    group_name TEXT DEFAULT '',
    proxy_id TEXT,
    status TEXT DEFAULT 'active',
    notes TEXT DEFAULT '',
    account_meta_enc BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_launched_at TEXT
);
CREATE TABLE IF NOT EXISTS proxies(
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    username_enc BLOB,
    password_enc BLOB,
    extra_enc BLOB,
    tags TEXT DEFAULT '',
    last_check_at TEXT,
    latency_ms INTEGER,
    country TEXT,
    city TEXT,
    enabled INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS cookies(
    profile_id TEXT PRIMARY KEY,
    engine TEXT,
    blob BLOB,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks(
    id TEXT PRIMARY KEY,
    recipe TEXT NOT NULL,
    profile_id TEXT,
    params TEXT DEFAULT '{}',
    schedule TEXT,
    status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    profile_id TEXT,
    message TEXT NOT NULL,
    screenshot_path TEXT
);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    """线程安全（sqlite 短连接 + 超时等待）的存储封装。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """旧库安全迁移：补充后续版本新增的列（存在则跳过）。"""
        cols = {r[1] for r in
                conn.execute("PRAGMA table_info(proxies)").fetchall()}
        if "extra_enc" not in cols:
            conn.execute("ALTER TABLE proxies ADD COLUMN extra_enc BLOB")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ================= profiles =================

    def create_profile(self, name: str, engine: str, seed: str,
                       platform_tags: str = "", group_name: str = "",
                       proxy_id: str | None = None, notes: str = "",
                       status: str = "active") -> dict:
        pid = new_id()
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO profiles(id, name, engine, seed, platform_tags,"
                " group_name, proxy_id, status, notes, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pid, name, engine, seed, platform_tags, group_name, proxy_id,
                 status, notes, now, now),
            )
        return self.get_profile(pid)

    def get_profile(self, profile_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM profiles WHERE id=?", (profile_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_profiles(self, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM profiles"
        args: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        sql += " ORDER BY created_at DESC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def update_profile(self, profile_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {"name", "engine", "seed", "platform_tags", "group_name",
                   "proxy_id", "status", "notes", "account_meta_enc",
                   "last_launched_at"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"非法字段: {bad}")
        fields["updated_at"] = _now()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE profiles SET {sets} WHERE id=?",
                (*fields.values(), profile_id),
            )

    def delete_profile(self, profile_id: str) -> None:
        """删除档案并级联清理 cookies 行（cookies 表以 profile_id 为 PK）。"""
        with self._conn() as conn:
            conn.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
            conn.execute("DELETE FROM cookies WHERE profile_id=?", (profile_id,))

    # ================= proxies =================

    def create_proxy(self, type: str, host: str, port: int,
                     username_enc: bytes | None = None,
                     password_enc: bytes | None = None,
                     extra_enc: bytes | None = None,
                     tags: str = "", enabled: int = 1) -> dict:
        pid = new_id()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO proxies(id, type, host, port, username_enc,"
                " password_enc, extra_enc, tags, enabled)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, type, host, port, username_enc, password_enc,
                 extra_enc, tags, enabled),
            )
        return self.get_proxy(pid)

    def get_proxy(self, proxy_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM proxies WHERE id=?", (proxy_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_proxies(self, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM proxies"
        args: tuple[Any, ...] = ()
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY host, port"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def update_proxy(self, proxy_id: str, **fields: Any) -> None:
        allowed = {"type", "host", "port", "username_enc", "password_enc",
                   "extra_enc", "tags", "last_check_at", "latency_ms",
                   "country", "city", "enabled"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"非法字段: {bad}")
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE proxies SET {sets} WHERE id=?",
                (*fields.values(), proxy_id),
            )

    def delete_proxy(self, proxy_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM proxies WHERE id=?", (proxy_id,))
            # 档案解绑而非级联删除档案
            conn.execute(
                "UPDATE profiles SET proxy_id=NULL WHERE proxy_id=?",
                (proxy_id,),
            )

    # ================= cookies =================

    def save_cookies(self, profile_id: str, engine: str, blob: bytes) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO cookies(profile_id, engine, blob, updated_at)"
                " VALUES (?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET"
                " engine=excluded.engine, blob=excluded.blob,"
                " updated_at=excluded.updated_at",
                (profile_id, engine, blob, _now()),
            )

    def get_cookies(self, profile_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM cookies WHERE profile_id=?", (profile_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_cookies(self, profile_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM cookies WHERE profile_id=?", (profile_id,)
            )

    # ================= tasks =================

    def create_task(self, recipe: str, profile_id: str | None = None,
                    params: dict | None = None,
                    schedule: str | None = None) -> dict:
        tid = new_id()
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tasks(id, recipe, profile_id, params, schedule,"
                " status, attempts, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,0,?,?)",
                (tid, recipe, profile_id, json.dumps(params or {},
                                                     ensure_ascii=False),
                 schedule, "pending", now, now),
            )
        return self.get_task(tid)

    def get_task(self, task_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_tasks(self, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM tasks"
        args: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        sql += " ORDER BY created_at DESC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def update_task(self, task_id: str, **fields: Any) -> None:
        allowed = {"recipe", "profile_id", "params", "schedule", "status",
                   "attempts", "last_error"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"非法字段: {bad}")
        if not fields:
            return
        fields["updated_at"] = _now()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE tasks SET {sets} WHERE id=?",
                (*fields.values(), task_id),
            )

    def delete_task(self, task_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    # ================= logs =================

    def add_log(self, level: str, source: str, message: str,
                profile_id: str | None = None,
                screenshot_path: str | None = None) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO logs(ts, level, source, profile_id, message,"
                " screenshot_path) VALUES (?,?,?,?,?,?)",
                (_now(), level, source, profile_id, message, screenshot_path),
            )
            return int(cur.lastrowid)

    def list_logs(self, limit: int = 200, profile_id: str | None = None,
                  level: str | None = None) -> list[dict]:
        sql = "SELECT * FROM logs"
        cond: list[str] = []
        args: list[Any] = []
        if profile_id:
            cond.append("profile_id=?")
            args.append(profile_id)
        if level:
            cond.append("level=?")
            args.append(level)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def prune_logs(self, keep: int = 5000) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM logs WHERE id <= ("
                " SELECT MAX(id) - ? FROM logs)", (keep,),
            )
            return cur.rowcount

    # ================= settings =================

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
