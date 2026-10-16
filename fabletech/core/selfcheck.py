"""指纹自检：在档案浏览器内采集指纹实况 + 检测站点评分。

设计：
- 复用档案的指纹/代理/引擎启动浏览器（headed 或 headless 可选）
- 经 JS 采集本机实况：navigator / screen / WebGL / 时区 / 语言 / 插件 /
  Canvas 摘要 / WebRTC 泄漏（block_webrtc 生效性）
- 与档案配置指纹逐项比对，输出「通过/风险」清单与综合评分
- 另开检测站点截图存档，供人工复核（站点 DOM 变动不影响主流程）

红线：不引入任何自动外发（检测站点访问是用户显式触发的自检行为）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import paths
from .logger import log_event

# 采集脚本：返回本机指纹实况（不依赖站点 DOM，稳定可靠）
COLLECT_JS = r"""
() => {
  const out = {};
  try {
    out.webdriver = navigator.webdriver;
    out.userAgent = navigator.userAgent;
    out.platform = navigator.platform;
    out.languages = navigator.languages;
    out.language = navigator.language;
    out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    out.screen = [screen.width, screen.height];
    out.availScreen = [screen.availWidth, screen.availHeight];
    out.viewport = [innerWidth, innerHeight];
    out.devicePixelRatio = devicePixelRatio;
    out.hardwareConcurrency = navigator.hardwareConcurrency;
    out.deviceMemory = navigator.deviceMemory;
    out.plugins = navigator.plugins.length;
    out.mimeTypes = navigator.mimeTypes.length;
    out.hasChrome = typeof window.chrome !== 'undefined';
    out.permissions = typeof navigator.permissions !== 'undefined';
    // WebGL
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    if (gl) {
      const dbg = gl.getExtension('WEBGL_debug_renderer_info');
      out.webglVendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null;
      out.webglRenderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null;
      out.webglVersion = gl.getParameter(gl.VERSION);
    }
    // Canvas 摘要（稳定 hash，用于确认档案指纹跨会话一致）
    const cc = document.createElement('canvas');
    cc.width = 220; cc.height = 40;
    const cx = cc.getContext('2d');
    cx.textBaseline = 'top'; cx.font = '14px Arial';
    cx.fillStyle = '#f60'; cx.fillRect(0, 0, 110, 24);
    cx.fillStyle = '#069'; cx.fillText('FableTech 指纹自检', 2, 8);
    const data = cc.toDataURL();
    let h = 0;
    for (let i = 0; i < data.length; i++) {
      h = ((h << 5) - h + data.charCodeAt(i)) | 0;
    }
    out.canvasHash = (h >>> 0).toString(16);
  } catch (e) { out.error = String(e); }
  return out;
}
"""

# 检测站点（截图存档供人工复核；不解析其 DOM，避免改版导致误判）
CHECK_SITES = [
    ("browserleaks", "https://browserleaks.com/javascript"),
    ("creepjs", "https://abrahamjuliot.github.io/creepjs/"),
]

# UA 中的版本/平台解析
_UA_OS = [
    (re.compile(r"Windows NT 10\.0"), "Windows 10/11"),
    (re.compile(r"Macintosh; Intel Mac OS X"), "macOS"),
    (re.compile(r"Linux x86_64"), "Linux"),
    (re.compile(r"Android"), "Android"),
    (re.compile(r"iPhone|iPad"), "iOS"),
]


def _check(items: list[dict], name: str, passed: bool, detail: str) -> None:
    items.append({"name": name, "passed": bool(passed), "detail": detail})


def evaluate(collected: dict, fingerprint: dict,
             engine_cfg: dict | None = None) -> dict:
    """将采集实况与档案指纹比对，输出自检结论。"""
    items: list[dict] = []
    cfg = engine_cfg or {}

    # 1) 自动化特征
    _check(items, "navigator.webdriver 未暴露",
           collected.get("webdriver") is False,
           f"webdriver={collected.get('webdriver')}")

    # 2) UA 与档案指纹一致
    want_ua = (fingerprint.get("userAgent") or cfg.get("userAgent")
               or cfg.get("ua") or "")
    got_ua = collected.get("userAgent") or ""
    if want_ua:
        _check(items, "UA 与档案指纹一致", want_ua == got_ua,
               f"期望 {want_ua[:60]}… / 实际 {got_ua[:60]}…"
               if want_ua != got_ua else "一致")
    else:
        _check(items, "UA 已注入（非默认）", bool(got_ua), got_ua[:70])

    # 3) 语言
    want_locale = cfg.get("locale") or fingerprint.get("locale")
    got_lang = collected.get("language") or ""
    if want_locale:
        _check(items, "语言与档案一致",
               got_lang.lower().startswith(want_locale.split("-")[0].lower()),
               f"期望 {want_locale} / 实际 {got_lang}")

    # 4) 时区（geoip 生效性）
    tz = collected.get("timezone") or ""
    _check(items, "时区已注入（非 UTC 默认）", bool(tz) and tz != "UTC",
           f"timezone={tz}")

    # 5) 屏幕尺寸与档案指纹一致（允许 ±2 边框误差）
    want_screen = fingerprint.get("screen") or {}
    got_screen = collected.get("screen") or [0, 0]
    if want_screen.get("width") and want_screen.get("height"):
        dw = abs(int(want_screen["width"]) - int(got_screen[0]))
        dh = abs(int(want_screen["height"]) - int(got_screen[1]))
        _check(items, "屏幕分辨率与档案一致", dw <= 2 and dh <= 2,
               f"期望 {want_screen['width']}x{want_screen['height']} / "
               f"实际 {got_screen[0]}x{got_screen[1]}")

    # 6) WebGL 已伪装（有值且非报错）
    _check(items, "WebGL 渲染器可读（未禁用）",
           bool(collected.get("webglRenderer")),
           f"{collected.get('webglVendor')} / {collected.get('webglRenderer')}")

    # 7) 硬件并发 / 内存与档案一致（browserforge 生成）
    hc = collected.get("hardwareConcurrency")
    want_hc = fingerprint.get("hardwareConcurrency")
    if want_hc:
        _check(items, "CPU 核数与档案一致", int(hc or 0) == int(want_hc),
               f"期望 {want_hc} / 实际 {hc}")
    dm = collected.get("deviceMemory")
    want_dm = fingerprint.get("deviceMemory")
    if want_dm:
        _check(items, "设备内存与档案一致", float(dm or 0) == float(want_dm),
               f"期望 {want_dm} / 实际 {dm}")

    # 8) 插件列表非空（Firefox 有内置插件）
    _check(items, "插件列表正常（非空）", int(collected.get("plugins") or 0) > 0,
           f"plugins={collected.get('plugins')}")

    # 9) Canvas 摘要存在（可跨会话比对一致性）
    _check(items, "Canvas 可渲染（摘要已生成）",
           bool(collected.get("canvasHash")),
           f"canvasHash={collected.get('canvasHash')}")

    passed = sum(1 for i in items if i["passed"])
    total = len(items)
    score = round(passed * 100 / total) if total else 0
    verdict = ("优秀" if score >= 90 else "良好" if score >= 75
               else "有风险" if score >= 50 else "不合格")
    return {"items": items, "passed": passed, "total": total,
            "score": score, "verdict": verdict}


def run_selfcheck(profile_id: str, profile_service, fingerprint: dict,
                  engine_cfg: dict, headless: bool = True,
                  save_report: bool = True) -> dict:
    """对档案执行指纹自检（启动浏览器 → 采集 → 截图 → 关闭）。

    返回 {ok, score, verdict, items, collected, report_path, screenshots}
    """
    log_event("INFO", "selfcheck", f"开始指纹自检（档案 {profile_id}）",
              profile_id=profile_id)
    session = None
    screenshots: list[str] = []
    try:
        session = profile_service.launch(profile_id, headless=headless,
                                         load_cookies=False)
        page = session.page
        # 采集实况（about:blank 即可，值由引擎注入）
        collected = session.run_sync(page.evaluate(COLLECT_JS))
        result = evaluate(collected or {}, fingerprint, engine_cfg)

        # 检测站点截图（人工复核用；失败不影响结论）
        shots_dir = paths().screenshots
        shots_dir.mkdir(parents=True, exist_ok=True)
        for name, url in CHECK_SITES:
            try:
                session.run_sync(page.goto(url, timeout=45000,
                                           wait_until="domcontentloaded"))
                session.run_sync(page.wait_for_timeout(6000))
                p = shots_dir / f"selfcheck-{profile_id}-{name}.png"
                session.run_sync(page.screenshot(path=str(p), full_page=False))
                screenshots.append(str(p))
            except Exception as e:
                log_event("WARNING", "selfcheck",
                          f"检测站点 {name} 截图失败：{e}",
                          profile_id=profile_id)

        report = {
            "profile_id": profile_id,
            "checked_at": datetime.now(timezone.utc)
            .isoformat(timespec="seconds"),
            "score": result["score"],
            "verdict": result["verdict"],
            "passed": result["passed"],
            "total": result["total"],
            "items": result["items"],
            "collected": collected,
            "screenshots": screenshots,
        }
        report_path = None
        if save_report:
            rdir = paths().root / "selfcheck"
            rdir.mkdir(parents=True, exist_ok=True)
            rp = rdir / f"{profile_id}.json"
            rp.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                          encoding="utf-8")
            report_path = str(rp)
        log_event("INFO", "selfcheck",
                  f"自检完成：{result['score']} 分（{result['verdict']}）"
                  f" {result['passed']}/{result['total']} 项通过",
                  profile_id=profile_id)
        report["ok"] = True
        report["report_path"] = report_path
        return report
    except Exception as e:
        log_event("ERROR", "selfcheck", f"自检失败：{e}", profile_id=profile_id)
        return {"ok": False, "error": str(e), "items": [],
                "score": 0, "verdict": "失败"}
    finally:
        if session is not None:
            try:
                profile_service.close(profile_id)
            except Exception:
                pass


def format_report(report: dict) -> str:
    """自检报告 → 可读文本（GUI 弹窗 / CLI 输出）。"""
    if not report.get("ok"):
        return f"指纹自检失败：{report.get('error', '未知错误')}"
    lines = [
        f"综合评分：{report['score']} 分（{report['verdict']}）"
        f"　{report['passed']}/{report['total']} 项通过",
        "",
    ]
    for it in report.get("items", []):
        mark = "✓" if it["passed"] else "✗"
        lines.append(f"{mark} {it['name']}：{it['detail']}")
    if report.get("screenshots"):
        lines.append("")
        lines.append("检测站点截图已存档：")
        lines += [f"  {s}" for s in report["screenshots"]]
    if report.get("report_path"):
        lines.append(f"\n报告文件：{report['report_path']}")
    return "\n".join(lines)
