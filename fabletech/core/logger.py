"""统一日志：Python logging + 文件双写 + SQLite 记录 + 凭证脱敏。

红线：
- 代理 URL 在日志中必须掩码（socks5://u***:p***@host:port）
- 密码/密钥等敏感字段永不进日志（调用方负责不传入；这里再对已知模式脱敏）
"""

from __future__ import annotations

import logging
import re
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..config import paths

_LOGGER_NAME = "fabletech"
_default_handler: logging.Handler | None = None

# user:pass@host 形式的凭证掩码
_CRED_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)://"
                      r"(?P<user>[^:@/]+):(?P<pass>[^@/]+)@"
                      r"(?P<host>[^@/\s]+)")


def mask_credentials(url: str) -> str:
    """socks5://user:pass@host:port → socks5://u***:p***@host:port"""
    if not url:
        return url

    def _sub(m: re.Match[str]) -> str:
        return f"{m.group('scheme')}://u***:p***@{m.group('host')}"

    return _CRED_RE.sub(_sub, url)


def sanitize(text: str) -> str:
    """对日志文本做通用脱敏（凭证掩码 + 常见敏感键值对）。"""
    if not text:
        return text
    text = mask_credentials(text)
    # password=xxx / "password": "xxx" / master_password=...
    text = re.sub(
        r"(?i)(password|passwd|pwd|master_password|secret|token|api_key)"
        r"(\"\s*[:=]\s*\"[^\"]*\"|'\s*[:=]\s*'[^']*'|\s*[:=]\s*\S+)",
        r"\1=***",
        text,
    )
    return text


def _setup_file_handler(logs_dir: Path) -> logging.Handler:
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(logs_dir / "fabletech.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(source)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    return handler


class _DBHandler(logging.Handler):
    """写入 SQLite logs 表；表不存在时静默跳过（避免日志循环依赖 storage）。"""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            p = paths()
            if not p.db.exists():
                return
            with self._lock, sqlite3.connect(p.db) as conn:
                conn.execute(
                    "INSERT INTO logs(ts, level, source, profile_id, message, screenshot_path)"
                    " VALUES (?,?,?,?,?,?)",
                    (
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        record.levelname,
                        getattr(record, "source", record.name),
                        getattr(record, "profile_id", None),
                        sanitize(msg),
                        getattr(record, "screenshot_path", None),
                    ),
                )
        except sqlite3.Error:
            pass  # 日志写入失败（库未就绪等）不得影响主流程
        except Exception as e:  # noqa: BLE001 - 兜底：脱敏后记录到 stderr
            print(f"[fabletech-logger] 日志写入异常：{e}", file=sys.stderr)


class _SanitizingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: sanitize(str(v)) for k, v in record.args.items()}
            else:
                record.args = tuple(sanitize(str(a)) for a in record.args)
        record.msg = sanitize(record.getMessage())
        record.args = ()
        return True


def get_logger() -> logging.Logger:
    """获取应用级 logger（单例，带文件+SQLite 双 handler）。"""
    global _default_handler
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        _default_handler = _setup_file_handler(paths().logs)
        logger.addHandler(_default_handler)
        logger.addHandler(_DBHandler())
        logger.addFilter(_SanitizingFilter())
    return logger


def log_event(level: str, source: str, message: str,
              profile_id: str | None = None,
              screenshot_path: str | None = None) -> None:
    """带 source/profile_id/screenshot 的日志入口。"""
    logger = get_logger()
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        message,
        extra={"source": source, "profile_id": profile_id,
               "screenshot_path": screenshot_path},
    )
