"""路径解析：便携模式（./data）与安装模式（%LOCALAPPDATA%/FableTech）。

便携模式判定（任一满足）：
1. 应用根目录存在 portable.flag
2. 根目录可写（便携场景，数据不落用户目录）

所有路径由此模块统一派生，禁止散落拼接。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "FableTech"
PORTABLE_FLAG = "portable.flag"

_db_dir_override: Path | None = None


def app_root() -> Path:
    """应用根目录：打包后为 exe 所在目录，开发时为包上级目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def is_portable() -> bool:
    """是否便携模式：portable.flag 存在，或根目录可写。"""
    root = app_root()
    if (root / PORTABLE_FLAG).exists():
        return True
    if _db_dir_override is not None:
        return True
    try:
        root.stat()
        # 探测写入权限：创建临时文件失败即为不可写
        probe = root / ".fabletech_probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True
        except OSError:
            return False
    except OSError:
        return False


@dataclass(frozen=True)
class Paths:
    root: Path          # 数据根目录（./data 或 %LOCALAPPDATA%/FableTech）
    db: Path            # fabletech.db
    cookies: Path       # 加密 Cookie 仓目录
    screenshots: Path  # 失败截图 / waiting_human 截图
    logs: Path          # 日志文件目录
    user_data: Path     # 各档案浏览器 user_data_dir 的父目录
    backups: Path       # 导出档案包等
    bin: Path           # 运行时下载的二进制（mihomo 中转内核）
    relays: Path        # 本地中转进程的配置与日志


def _paths() -> Paths:
    if _db_dir_override is not None:
        root = _db_dir_override
    elif is_portable():
        root = app_root() / "data"
    else:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            root = Path(local) / APP_NAME
        else:  # 非 Windows 兜底
            root = Path.home() / f".{APP_NAME.lower()}"
    return Paths(
        root=root,
        db=root / "fabletech.db",
        cookies=root / "cookies",
        screenshots=root / "screenshots",
        logs=root / "logs",
        user_data=root / "user_data",
        backups=root / "backups",
        bin=root / "bin",
        relays=root / "relays",
    )


def paths() -> Paths:
    return _paths()


def ensure_dirs(p: Paths | None = None) -> Paths:
    p = p or paths()
    for d in (p.root, p.cookies, p.screenshots, p.logs, p.user_data,
              p.backups, p.relays):
        d.mkdir(parents=True, exist_ok=True)
    return p


def profile_user_data_dir(profile_id: str) -> Path:
    """档案专属浏览器数据目录。"""
    return paths().user_data / profile_id


def set_data_dir_override(path: Path) -> None:
    """仅测试用：强制指定数据目录（便携判定短路为 True）。"""
    global _db_dir_override
    _db_dir_override = Path(path)


def clear_data_dir_override() -> None:
    global _db_dir_override
    _db_dir_override = None
