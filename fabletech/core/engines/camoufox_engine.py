"""Camoufox 引擎（主引擎，MPL-2.0，Firefox 定制）。

启动参数依据 camoufox 0.5.8 实测签名（核实命令见 BUILD_NOTES.md）：
  launch_options(*, os, geoip, humanize, locale, screen, window, proxy,
                 headless, persistent_context, user_data_dir, ...)
AsyncCamoufox(**kwargs) → persistent_context=True 时返回持久化 BrowserContext。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from ..logger import log_event
from .base import BrowserEngine, EngineSession, LaunchSpec, _run_loop_in_thread


class EngineUnavailableError(RuntimeError):
    """引擎不可用（依赖缺失/浏览器未安装）。"""


class CamoufoxEngine(BrowserEngine):
    name = "camoufox"

    def available(self) -> tuple[bool, str]:
        try:
            import camoufox  # noqa: F401
        except ImportError:
            return False, ("未安装 camoufox。请在「设置 → 引擎运行时」点击"
                           "「一键安装运行时」，或执行：pip install camoufox")
        try:
            from camoufox.pkgman import installed_verstr
            installed_verstr()
        except Exception as e:
            return False, (f"camoufox 浏览器未下载：{e}。"
                           "请在「设置 → 引擎运行时」点击「一键安装运行时」。")
        return True, ""

    def ensure_runtime(self) -> bool:
        """pip 安装 camoufox（若缺）+ camoufox fetch 下载浏览器。"""
        if not self.available()[0]:
            try:
                import camoufox  # noqa: F401
            except ImportError:
                log_event("INFO", "engine.camoufox",
                          "正在安装 camoufox 包…")
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "camoufox"],
                    capture_output=True, text=True, timeout=600,
                )
                if proc.returncode != 0:
                    log_event("ERROR", "engine.camoufox",
                              f"camoufox 安装失败：{proc.stderr[-300:]}")
                    return False
        proc = subprocess.run(
            [sys.executable, "-m", "camoufox", "fetch"],
            capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode != 0:
            log_event("ERROR", "engine.camoufox",
                      f"浏览器下载失败：{proc.stderr[-300:] or proc.stdout[-300:]}")
            return False
        log_event("INFO", "engine.camoufox", "camoufox 浏览器下载完成")
        return True

    # ---------- 启动 ----------

    def launch(self, spec: LaunchSpec) -> EngineSession:
        ok, hint = self.available()
        if not ok:
            raise EngineUnavailableError(hint)

        from camoufox import AsyncCamoufox
        from camoufox.fingerprints import Screen

        cfg = spec.engine_cfg
        screen = cfg.get("screen") or {}
        options: dict = {
            "persistent_context": True,
            "user_data_dir": str(Path(spec.user_data_dir).resolve()),
            "headless": spec.headless,
            "os": cfg.get("os", "windows"),
            "locale": cfg.get("locale", "en-US"),
            "humanize": cfg.get("humanize", True),
            "screen": Screen(
                min_width=screen.get("minWidth", 800),
                max_width=screen.get("maxWidth", 1680),
                min_height=screen.get("minHeight", 600),
                max_height=screen.get("maxHeight", 1050),
            ),
            "window": (
                (cfg.get("viewport") or {}).get("width", 1680),
                (cfg.get("viewport") or {}).get("height", 1050),
            ),
        }
        if spec.proxy:
            # proxy 传 dict；camoufox 内部会解析出口 IP/时区（_resolve_proxy_geo）
            options["proxy"] = {
                "server": spec.proxy.url(),
                **({"username": spec.proxy.username} if spec.proxy.username else {}),
                **({"password": spec.proxy.password} if spec.proxy.password else {}),
            }
            options["geoip"] = True
        if spec.extra.get("block_images"):
            options["block_images"] = True

        loop = _run_loop_in_thread()

        async def _async_launch():
            cm = AsyncCamoufox(**options)
            try:
                ctx = await cm.__aenter__()
            except BaseException:
                # 关闭 playwright 连接，避免驱动进程泄漏
                try:
                    await cm.__aexit__(None, None, None)
                finally:
                    raise
            page = await ctx.new_page()
            return ctx, page, cm

        try:
            ctx, page, cm = asyncio.run_coroutine_threadsafe(
                _async_launch(), loop).result(timeout=120)
        except BaseException as e:
            loop.call_soon_threadsafe(loop.stop)
            raise EngineUnavailableError(
                f"camoufox 启动失败：{e}。若浏览器未下载，请先在设置页"
                "「一键安装运行时」。") from e

        session = EngineSession(self.name, ctx, page, loop)
        session._close_cm = cm  # 保留上下文管理器引用供 close 用
        log_event("INFO", "engine.camoufox",
                  f"档案 {spec.profile_id} 已启动"
                  f"（代理：{spec.proxy.masked_url() if spec.proxy else '无'}）",
                  profile_id=spec.profile_id)
        return session

    def close(self, session: EngineSession) -> None:
        cm = getattr(session, "_close_cm", None)
        if cm is not None:
            try:
                session.run_sync(cm.__aexit__(None, None, None))
            except Exception:
                pass
        session.close()
