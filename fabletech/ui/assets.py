"""UI 资产加载（兼容 PyInstaller 冻结环境与开发模式）。

资产位置：
  开发模式：fabletech/ui/assets/
  冻结模式：<exe目录>/_internal/fabletech/ui/assets/（PyInstaller --add-data）
"""

from __future__ import annotations

import sys
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

LOGO_PNG = "logo.png"            # 原图（白底）
LOGO_ROUNDED = "logo_rounded.png"  # 圆角透明（深色 GUI）
LOGO_ICO = "logo.ico"            # 多尺寸窗口图标


def asset_path(name: str) -> Path:
    """资产文件路径（开发模式与冻结模式均可用）。"""
    p = ASSETS_DIR / name
    if p.exists():
        return p
    # 冻结环境兜底：PyInstaller 会把包数据放到 _internal/
    frozen = Path(getattr(sys, "_MEIPASS", ASSETS_DIR))
    return frozen / "fabletech" / "ui" / "assets" / name


def has_logo() -> bool:
    return asset_path(LOGO_PNG).exists()
