"""PyInstaller GUI 入口（绝对导入；包内模块本身使用相对导入）。"""

from fabletech.app import run_gui

if __name__ == "__main__":
    raise SystemExit(run_gui())
