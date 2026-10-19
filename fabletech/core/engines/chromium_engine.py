"""Patchright-Chromium 引擎（备选，能力受限：仅透传 UA/viewport，不做内核级伪造）。

- import patchright guard
- 系统已装 Chrome 时优先 channel="chrome"
- 持久化上下文：user_data_dir = 档案目录
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from ..logger import log_event
from .base import BrowserEngine, EngineSession, LaunchSpec, _run_loop_in_thread


class EngineUnavailableError(RuntimeError):
    """引擎不可用（依赖缺失/浏览器未安装）。"""


def _system_chrome_path() -> str | None:
    """探测系统安装的 Chrome/Edge（用于 channel 选择）。"""
    candidates = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return shutil.which("chrome") or shutil.which("google-chrome")


class ChromiumEngine(BrowserEngine):
    name = "chromium"

    def available(self) -> tuple[bool, str]:
        try:
            import patchright  # noqa: F401
        except ImportError:
            return False, ("未安装 patchright。请在「设置 → 引擎运行时」点击"
                           "「一键安装运行时」，或执行：pip install patchright")
        if not _system_chrome_path():
            return False, ("未检测到系统 Chrome。请安装 Chrome，或在设置页"
                           "「一键安装运行时」下载 Chromium 浏览器。")
        return True, ""

    def ensure_runtime(self) -> bool:
        """确保 patchright 与 Chromium 可用。

        冻结环境下不能用 [sys.executable, "-m", "patchright", ...]
        （会重启 GUI）；改为进程内驱动安装，失败则给出人工指引。
        """
        ok, _ = self.available()
        if ok:
            return True
        # 优先：系统已装 Chrome 时直接可用（available 已判定，走到这里说明没有）
        try:
            import patchright  # noqa: F401
        except ImportError:
            if getattr(sys, "frozen", False):
                log_event("ERROR", "engine.chromium",
                          "便携包内 patchright 缺失（环境异常），"
                          "请重新下载完整便携包")
                return False
            import subprocess
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "patchright"],
                capture_output=True, text=True, timeout=600,
            )
            if proc.returncode != 0:
                log_event("ERROR", "engine.chromium",
                          f"patchright 安装失败：{proc.stderr[-300:]}")
                return False
        # 进程内调用 patchright 驱动的 install（等价 CLI install chromium）
        try:
            from patchright._impl._driver import (
                compute_driver_executable, get_driver_env,
            )
            import subprocess
            node, cli = compute_driver_executable()
            proc = subprocess.run(
                [str(node), str(cli), "install", "chromium"],
                capture_output=True, text=True, timeout=1800,
                env=get_driver_env(),
            )
            if proc.returncode == 0:
                log_event("INFO", "engine.chromium", "Chromium 下载完成")
                return True
            log_event("ERROR", "engine.chromium",
                      f"Chromium 下载失败：{proc.stderr[-300:]}")
        except Exception as e:
            log_event("ERROR", "engine.chromium",
                      f"Chromium 安装异常：{e}。建议直接安装系统 Chrome "
                      "（https://www.google.com/chrome/）后点「重新检测」")
        return False

    def launch(self, spec: LaunchSpec) -> EngineSession:
        ok, hint = self.available()
        if not ok:
            raise EngineUnavailableError(hint)

        from patchright.async_api import async_playwright

        cfg = spec.engine_cfg
        viewport = cfg.get("viewport") or {"width": 1680, "height": 1050}
        chrome = _system_chrome_path()
        loop = _run_loop_in_thread()

        async def _async_launch():
            pw = await async_playwright().start()
            launch_kwargs: dict = {
                "headless": spec.headless,
                "no_viewport": True,
                "viewport": {"width": viewport["width"],
                             "height": viewport["height"]},
                "user_agent": cfg.get("user_agent") or None,
            }
            if chrome:
                launch_kwargs["executable_path"] = chrome
            if spec.proxy:
                launch_kwargs["proxy"] = {
                    "server": spec.proxy.url(),
                    **({"username": spec.proxy.username}
                       if spec.proxy.username else {}),
                    **({"password": spec.proxy.password}
                       if spec.proxy.password else {}),
                }
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(Path(spec.user_data_dir).resolve()),
                **launch_kwargs,
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            return ctx, page, pw

        try:
            ctx, page, pw = asyncio.run_coroutine_threadsafe(
                _async_launch(), loop).result(timeout=120)
        except BaseException as e:
            loop.call_soon_threadsafe(loop.stop)
            raise EngineUnavailableError(f"Chromium 引擎启动失败：{e}") from e

        session = EngineSession(self.name, ctx, page, loop)
        session._pw = pw
        log_event("INFO", "engine.chromium",
                  f"档案 {spec.profile_id} 已启动（Chromium 引擎，指纹能力受限）",
                  profile_id=spec.profile_id)
        return session

    def close(self, session: EngineSession) -> None:
        pw = getattr(session, "_pw", None)
        try:
            session.close()
        finally:
            if pw is not None:
                try:
                    session.run_sync(pw.stop())
                except Exception:
                    pass
