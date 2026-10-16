"""指纹服务：BrowserForge 封装，seed 确定性派生，输出统一指纹配置。

- 同 seed → 同指纹（档案跨会话指纹恒定）
- 依赖缺失时给出中文提示，不崩溃
"""

from __future__ import annotations

import json
import random
import secrets
from typing import Any

try:
    from browserforge.fingerprints import FingerprintGenerator
    _BROWSERFORGE_AVAILABLE = True
except ImportError:  # pragma: no cover - 运行时缺依赖
    FingerprintGenerator = None  # type: ignore[assignment,misc]
    _BROWSERFORGE_AVAILABLE = False

MISSING_DEPS_HINT = (
    "缺少指纹生成组件 browserforge。请在「设置 → 引擎运行时」中点击"
    "「一键安装运行时」，或执行：pip install browserforge"
)

_GENERATOR: Any = None


def new_seed() -> str:
    """生成新的档案指纹种子。"""
    return secrets.token_hex(8)


def _get_generator() -> Any:
    global _GENERATOR
    if _GENERATOR is None:
        _GENERATOR = FingerprintGenerator()
    return _GENERATOR


def browserforge_available() -> bool:
    return _BROWSERFORGE_AVAILABLE


def generate_fingerprint(seed: str | None = None) -> dict:
    """生成统一指纹配置。seed 缺省时随机。

    返回：
    {os, browser, screen, viewport, locale, timezone, fonts,
     hardwareConcurrency, deviceMemory, webgl, userAgent, seed}
    """
    if seed is None:
        seed = new_seed()
    if not _BROWSERFORGE_AVAILABLE:
        raise RuntimeError(MISSING_DEPS_HINT)

    # browserforge 无原生 seed 参数：临时种子化全局 random 驱动其采样，
    # 结束后恢复原状态，避免污染其他模块的随机源。
    state = random.getstate()
    try:
        random.seed(seed)
        raw = json.loads(_get_generator().generate().dumps())
    finally:
        random.setstate(state)

    screen = raw.get("screen") or {}
    nav = raw.get("navigator") or {}
    viewport = {
        "width": screen.get("width", 1680),
        "height": screen.get("height", 1050),
    }
    fp = {
        "seed": seed,
        "os": _detect_os(nav.get("userAgent", "")),
        "browser": _detect_browser(nav.get("userAgent", "")),
        "screen": {k: screen.get(k) for k in
                   ("width", "height", "availWidth", "availHeight",
                    "colorDepth", "pixelDepth", "devicePixelRatio")
                   if k in screen},
        "viewport": viewport,
        "locale": (nav.get("languages") or ["en-US"])[0],
        "timezone": "UTC",  # 由代理地理信息在 launch 编排时覆盖
        "fonts": raw.get("fonts") or [],
        "hardwareConcurrency": nav.get("hardwareConcurrency"),
        "deviceMemory": nav.get("deviceMemory"),
        "webgl": raw.get("videoCard") or raw.get("webgl") or {},
        "userAgent": nav.get("userAgent", ""),
        "raw_navigator": nav,
    }
    return fp


def _detect_os(ua: str) -> str:
    ua = ua.lower()
    if "windows" in ua:
        return "windows"
    if "mac os" in ua or "macintosh" in ua:
        return "macos"
    if "linux" in ua or "x11" in ua:
        return "linux"
    return "windows"


def _detect_browser(ua: str) -> str:
    ua = ua.lower()
    if "firefox" in ua:
        return "firefox"
    if "edg/" in ua:
        return "edge"
    if "chrome" in ua or "chromium" in ua:
        return "chrome"
    return "firefox"


def engine_config(fp: dict, engine: str) -> dict:
    """把统一指纹翻译成各引擎启动配置。

    camoufox: os / locale / timezone_id / screen / humanize 等（key 齐全，
               具体启动参数由 camoufox_engine 按核实后的签名过滤）
    cloak:    指纹 seed 透传
    chromium: UA/viewport 透传（能力受限）
    """
    screen = fp.get("screen") or {}
    common = {
        "user_agent": fp.get("userAgent", ""),
        "viewport": fp.get("viewport") or {"width": 1680, "height": 1050},
        "locale": fp.get("locale", "en-US"),
        "timezone_id": fp.get("timezone", "UTC"),
        "seed": fp.get("seed"),
    }
    if engine == "camoufox":
        return {
            **common,
            "os": fp.get("os", "windows"),
            "screen": {
                "maxWidth": screen.get("width", 1680),
                "maxHeight": screen.get("height", 1050),
                "minWidth": 800,
                "minHeight": 600,
            },
            "humanize": True,
        }
    if engine == "cloak":
        return {**common, "fingerprint_seed": fp.get("seed")}
    if engine == "chromium":
        # Patchright 透传 UA/viewport，不做内核级伪造（UI 已标注能力受限）
        return {**common}
    raise ValueError(f"未知引擎: {engine}")
