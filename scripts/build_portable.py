"""打包脚本：PyInstaller 构建 GUI + CLI → 组装 portable 目录 → zip。

产物：dist/FableTech-1.0.0-win-x64-portable.zip
目录结构：
  FableTech-portable/
    FableTech.exe          GUI 入口
    fabletech-cli.exe      CLI 入口
    _internal/             依赖
    data/                  空（首次运行生成）
    engines-runtime/       运行时安装说明
    portable.flag          便携模式标记
    README-portable.txt
浏览器二进制（~200MB）不预装：首次启动引擎时由 camoufox 自动下载。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
PORTABLE_NAME = "FableTech-portable"
ZIP_NAME = "FableTech-1.0.0-win-x64-portable.zip"

HIDDEN_IMPORTS = [
    "camoufox", "camoufox.pkgman", "camoufox.async_api", "camoufox.sync_api",
    "browserforge", "browserforge.fingerprints",
    "playwright", "playwright.sync_api", "playwright.async_api",
    "patchright", "patchright.sync_api", "patchright.async_api",
    "cryptography", "cryptography.hazmat.primitives.ciphers.aead",
    "customtkinter",
]

README_PORTABLE = """FableTech Browser 1.0.0 — 便携版（win-x64）
==========================================

快速开始
  1. 双击 FableTech.exe 启动 GUI（首次运行请设置主密码，≥8 位）。
  2. 命令行：fabletech-cli.exe --help 查看全部子命令。
     fabletech-cli.exe --password <主密码> create-profile --name 我的档案
     fabletech-cli.exe --password <主密码> launch <档案ID>

数据与便携
  - 本目录为便携模式：所有数据（数据库、加密 Cookie、日志、截图）
    保存在同级的 data/ 目录，卸载 = 删除整个文件夹。
  - 主密码丢失 = 数据不可恢复，请妥善保管。

引擎运行时（首次启动时按需安装）
  - Camoufox（主引擎）：首次启动档案时自动下载 Firefox 定制内核（约 1.3GB，
    一次性下载，受网络速度影响可能需 30 分钟以上）；
    也可手动执行：fabletech-cli.exe 所在环境的 python -m camoufox fetch
  - 详见 engines-runtime/README.txt

合规声明
  仅供管理团队自有账号；请遵守各平台服务条款与当地法律。
  无遥测、无自动更新；网络外发仅：显式 ip-api 地理查询、引擎下载、
  以及自动化访问的目标站点。
"""

RUNTIME_README = """引擎运行时安装说明
==================

浏览器二进制（Camoufox Firefox 定制内核，约 1.3GB）不预装在便携包内，
首次启动对应引擎时自动下载（有中文进度提示）。下载受网络影响，
可能需要 30 分钟以上，请耐心等待，勿中断。

手动安装方式（在装有 Python 3.11+ 的机器上）：
  pip install camoufox patchright playwright
  python -m camoufox fetch          # 下载 Camoufox 浏览器内核

或在 GUI「设置 → 引擎运行时」点击「一键安装运行时」。
"""


def run(cmd: list[str], cwd: Path = ROOT) -> None:
    print(f"[build] $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=cwd)
    if proc.returncode != 0:
        raise SystemExit(f"命令失败（exit={proc.returncode}）：{' '.join(cmd)}")


def build_exe(entry: str, name: str, windowed: bool) -> None:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onedir", "--clean",
        "--name", name,
        "--distpath", str(DIST), "--workpath", str(BUILD / f"_work_{name}"),
        "--specpath", str(BUILD / f"_spec_{name}"),
        "--collect-all", "camoufox",
        "--collect-all", "browserforge",
        "--collect-all", "apify_fingerprint_datapoints",
        "--collect-all", "language_tags",
        "--collect-all", "patchright",
        "--collect-submodules", "playwright",
        # UI 资产（Logo）与 exe 图标
        "--add-data", f"{ROOT / 'fabletech' / 'ui' / 'assets'};fabletech/ui/assets",
        "--icon", str(ROOT / "fabletech" / "ui" / "assets" / "logo.ico"),
    ]
    for h in HIDDEN_IMPORTS:
        args += ["--hidden-import", h]
    if windowed:
        args.append("--windowed")
    args.append(entry)
    run(args)


def main() -> int:
    print("== FableTech 便携包构建 ==")
    # 沙箱安全策略禁止大批量删除；PyInstaller 重建同名输出目录会删旧产物
    # （被拦截）。方案：每次构建用唯一名称（时间戳），组装 portable 时
    # 再复制/重命名为稳定的 FableTech.exe / fabletech-cli.exe。
    import uuid
    DIST.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(exist_ok=True)
    tag = f"{__import__('time').strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"

    # 1) PyInstaller 构建 GUI + CLI（入口脚本用绝对导入）
    gui_name = f"FableTech-b{tag}"
    cli_name = f"fabletech-cli-b{tag}"
    build_exe("scripts/entry_gui.py", gui_name, windowed=True)
    build_exe("scripts/entry_cli.py", cli_name, windowed=False)

    gui_dir = DIST / gui_name
    cli_dir = DIST / cli_name
    for d in (gui_dir, cli_dir):
        if not d.exists():
            raise SystemExit(f"构建产物缺失：{d}")

    # 2) 组装 portable 目录（唯一命名）
    portable = DIST / f"_pkg_{tag}"
    shutil.copytree(gui_dir, portable)
    # 重命名为稳定 exe 名（_internal 等其余文件保持原样）
    gui_exe = gui_dir / f"{gui_name}.exe"
    shutil.copy2(gui_exe, portable / "FableTech.exe")
    shutil.copy2(cli_dir / f"{cli_name}.exe",
                 portable / "fabletech-cli.exe")

    # 3) 便携标记 + 空数据目录 + 运行时说明
    (portable / "portable.flag").write_text(
        "portable mode: data lives beside this exe in ./data/\n",
        encoding="utf-8")
    (portable / "data").mkdir(exist_ok=True)
    rt = portable / "engines-runtime"
    rt.mkdir(exist_ok=True)
    (rt / "README.txt").write_text(RUNTIME_README, encoding="utf-8")
    if (ROOT / "requirements-runtime.txt").exists():
        shutil.copy2(ROOT / "requirements-runtime.txt",
                     rt / "requirements-runtime.txt")
    (portable / "README-portable.txt").write_text(README_PORTABLE,
                                                  encoding="utf-8")

    # 4) zip（zip 内统一 FableTech-portable/ 前缀，与源目录名无关）
    zip_path = DIST / ZIP_NAME
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(portable.rglob("*")):
            if f.is_file():
                zf.write(f, f"{PORTABLE_NAME}/{f.relative_to(portable)}")
    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"\n== 构建完成 ==\n便携目录：{portable}\n压缩包：{zip_path}"
          f"（{size_mb:.1f} MB）")
    print("提示：portable 目录为唯一命名（避免批量删除被安全策略拦截），"
          "正式交付直接使用 zip 包。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
