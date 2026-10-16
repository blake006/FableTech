"""指纹自检单测：评估逻辑 / 报告格式化 / run_selfcheck（mock 会话）。"""

from unittest.mock import AsyncMock, MagicMock

from fabletech.core.selfcheck import (
    COLLECT_JS,
    evaluate,
    format_report,
    run_selfcheck,
)


def _fp():
    return {
        "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/156.0",
        "locale": "en-US",
        "screen": {"width": 1920, "height": 1080},
        "hardwareConcurrency": 8,
        "deviceMemory": 8,
    }


def _collected(**over):
    base = {
        "webdriver": False,
        "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/156.0",
        "platform": "Win32",
        "language": "en-US",
        "languages": ["en-US", "en"],
        "timezone": "America/New_York",
        "screen": [1920, 1080],
        "viewport": [1680, 1050],
        "hardwareConcurrency": 8,
        "deviceMemory": 8,
        "plugins": 5,
        "webglVendor": "Google Inc.",
        "webglRenderer": "ANGLE (NVIDIA GeForce GTX 1660)",
        "canvasHash": "abc123",
    }
    base.update(over)
    return base


def test_evaluate_all_pass():
    r = evaluate(_collected(), _fp(), {"locale": "en-US"})
    assert r["score"] == 100
    assert r["verdict"] == "优秀"
    assert r["passed"] == r["total"] >= 8


def test_evaluate_webdriver_leak_fails():
    r = evaluate(_collected(webdriver=True), _fp(), {})
    item = next(i for i in r["items"] if "webdriver" in i["name"])
    assert item["passed"] is False
    assert r["score"] < 100


def test_evaluate_ua_mismatch_fails():
    r = evaluate(_collected(userAgent="Mozilla/5.0 Chrome/120"), _fp(), {})
    item = next(i for i in r["items"] if "UA" in i["name"])
    assert item["passed"] is False


def test_evaluate_timezone_default_fails():
    r = evaluate(_collected(timezone="UTC"), _fp(), {})
    item = next(i for i in r["items"] if "时区" in i["name"])
    assert item["passed"] is False


def test_evaluate_screen_mismatch_fails():
    r = evaluate(_collected(screen=[1366, 768]), _fp(), {})
    item = next(i for i in r["items"] if "屏幕" in i["name"])
    assert item["passed"] is False


def test_evaluate_cpu_memory_mismatch():
    r = evaluate(_collected(hardwareConcurrency=4, deviceMemory=4), _fp(), {})
    cpu = next(i for i in r["items"] if "CPU" in i["name"])
    mem = next(i for i in r["items"] if "内存" in i["name"])
    assert cpu["passed"] is False and mem["passed"] is False


def test_format_report_text():
    r = evaluate(_collected(), _fp(), {"locale": "en-US"})
    report = {"ok": True, "score": r["score"], "verdict": r["verdict"],
              "passed": r["passed"], "total": r["total"], "items": r["items"],
              "screenshots": ["C:/x/creepjs.png"],
              "report_path": "C:/x/report.json"}
    text = format_report(report)
    assert "综合评分" in text
    assert "✓" in text
    assert "creepjs.png" in text


def test_format_report_failure():
    text = format_report({"ok": False, "error": "浏览器未下载"})
    assert "失败" in text and "浏览器未下载" in text


def test_collect_js_no_external_deps():
    # 采集脚本自包含（不引用外部变量/函数）
    assert "navigator.webdriver" in COLLECT_JS
    assert "canvasHash" in COLLECT_JS
    assert "WEBGL_debug_renderer_info" in COLLECT_JS


class _FakePage:
    def __init__(self, collected):
        self._collected = collected
        self.goto_calls = []

    def evaluate(self, js):
        async def _coro():
            return self._collected
        return _coro()

    def goto(self, url, **kw):
        self.goto_calls.append(url)

        async def _coro():
            return None
        return _coro()

    def wait_for_timeout(self, ms):
        async def _coro():
            return None
        return _coro()

    def screenshot(self, path=None, **kw):
        async def _coro():
            from pathlib import Path
            Path(path).write_bytes(b"PNG")
            return None
        return _coro()


class _FakeSession:
    def __init__(self, page):
        self.page = page

    def run_sync(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)


def test_run_selfcheck_with_mock_session(tmp_path, monkeypatch):
    from fabletech.config import clear_data_dir_override, set_data_dir_override
    set_data_dir_override(tmp_path)
    try:
        page = _FakePage(_collected())
        svc = MagicMock()
        svc.launch.return_value = _FakeSession(page)
        report = run_selfcheck("pid123456789", svc, _fp(),
                               {"locale": "en-US"}, headless=True)
        assert report["ok"] is True
        assert report["score"] == 100
        assert report["report_path"]
        # 检测站点均访问过
        assert any("browserleaks" in u for u in page.goto_calls)
        assert any("creepjs" in u for u in page.goto_calls)
        # 报告文件已落盘
        from pathlib import Path
        assert Path(report["report_path"]).exists()
        # 会话已关闭
        svc.close.assert_called_once_with("pid123456789")
    finally:
        clear_data_dir_override()


def test_run_selfcheck_engine_failure_graceful(tmp_path):
    from fabletech.config import clear_data_dir_override, set_data_dir_override
    set_data_dir_override(tmp_path)
    try:
        svc = MagicMock()
        svc.launch.side_effect = RuntimeError("浏览器未下载")
        report = run_selfcheck("pid123456789", svc, _fp(), {})
        assert report["ok"] is False
        assert "浏览器未下载" in report["error"]
    finally:
        clear_data_dir_override()
