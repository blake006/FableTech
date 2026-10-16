"""PyInstaller CLI 入口（绝对导入；包内模块本身使用相对导入）。"""

from fabletech.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
