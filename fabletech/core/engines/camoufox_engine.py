"""Camoufox 引擎（主引擎，MPL-2.0，Firefox 定制）。

启动参数依据 camoufox 0.5.8 实测签名（核实命令见 BUILD_NOTES.md）：
  launch_options(*, os, geoip, humanize, locale, screen, window, proxy,
                 headless, persistent_context, user_data_dir, ...)
AsyncCamoufox(**kwargs) → persistent_context=True 时返回持久化 BrowserContext。
"""

from __future__ import annotations

import asyncio
import json as _json
import subprocess
import sys
from pathlib import Path

from ..logger import log_event
from .base import BrowserEngine, EngineSession, LaunchSpec, _run_loop_in_thread


class EngineUnavailableError(RuntimeError):
    """引擎不可用（依赖缺失/浏览器未安装）。"""


def _watermark_js(profile_name: str) -> str:
    """动态水印脚本（add_init_script 注入）：档案名浮于页面顶部。

    半透明、可拖动避让、不拦截点击（pointer-events: none）。
    """
    safe = _json.dumps(profile_name, ensure_ascii=False)
    return (
        "(function(){"
        "if(document.getElementById('__fabletech_wm__'))return;"
        "var d=document.createElement('div');"
        "d.id='__fabletech_wm__';"
        "d.textContent='FableTech · '+(" + safe + ");"
        "d.style.cssText='position:fixed;top:8px;right:8px;z-index:2147483647;"
        "padding:4px 10px;border-radius:6px;background:rgba(30,31,36,.72);"
        "color:#E8E9EC;font:12px/1.4 \"Microsoft YaHei UI\",sans-serif;"
        "pointer-events:none;user-select:none;';"
        "document.documentElement.appendChild(d);"
        "})();"
    )


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
        """确保 camoufox 包与浏览器内核可用。

        关键：浏览器下载必须**进程内**调用 CamoufoxFetcher.install()。
        冻结环境（便携包）下 sys.executable 是 FableTech.exe，
        执行 [sys.executable, "-m", "camoufox", "fetch"] 只会再启动
        一个 GUI，下载永远不会发生（v1.0.2 实际故障）。
        """
        ok, _ = self.available()
        if ok:
            return True
        # 1) 包缺失：非冻结环境用 pip 补装；冻结包已内置，走到这里说明环境异常
        try:
            import camoufox  # noqa: F401
        except ImportError:
            if getattr(sys, "frozen", False):
                log_event("ERROR", "engine.camoufox",
                          "便携包内 camoufox 包缺失（环境异常），"
                          "请重新下载完整便携包")
                return False
            log_event("INFO", "engine.camoufox", "正在安装 camoufox 包…")
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "camoufox"],
                capture_output=True, text=True, timeout=600,
            )
            if proc.returncode != 0:
                log_event("ERROR", "engine.camoufox",
                          f"camoufox 安装失败：{proc.stderr[-300:]}")
                return False

        # 2) 下载浏览器内核（进程内，约 1.3GB）
        log_event("INFO", "engine.camoufox",
                  "开始下载 camoufox 浏览器内核（约 1.3GB，"
                  "受网络影响可能需要 30 分钟以上，请勿关闭应用）…")
        try:
            from camoufox.pkgman import CamoufoxFetcher, installed_verstr
            CamoufoxFetcher().install()
            ver = installed_verstr()
            log_event("INFO", "engine.camoufox",
                      f"camoufox 浏览器下载完成：{ver}")
            return True
        except Exception as e:
            log_event("ERROR", "engine.camoufox",
                      f"浏览器下载失败：{e}。可尝试：①检查网络/代理；"
                      "②用其他机器下载后复制 %LOCALAPPDATA%\\camoufox 目录")
            return False

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
        # WebRTC 硬屏蔽（GeekezBrowser 借鉴：物理阻断本地 IP 泄漏路径，
        # disable_non_proxied_udp 策略）。默认开，可经设置关闭。
        options["block_webrtc"] = spec.extra.get("webrtc_block", True)

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
            # 动态水印（GeekezBrowser 借鉴）：档案名浮于页面顶部，
            # 多窗口并行时防误操作。经 add_init_script 注入。
            wm_name = spec.extra.get("profile_name")
            if wm_name and spec.extra.get("watermark", True):
                await ctx.add_init_script(_watermark_js(wm_name))
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
