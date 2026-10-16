"""python -m fabletech → GUI；python -m fabletech --cli 或子命令 → CLI。"""

from __future__ import annotations

import sys


def main() -> int:
    argv = sys.argv[1:]
    # 无参数或 --gui：启动 GUI；其余交 CLI
    if not argv or argv[0] in ("--gui", "-g"):
        from .app import run_gui
        return run_gui()
    from .cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
