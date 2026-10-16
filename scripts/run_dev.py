"""开发模式启动（免打包）：等价于 python -m fabletech。"""

from fabletech.app import run_gui

if __name__ == "__main__":
    raise SystemExit(run_gui())
