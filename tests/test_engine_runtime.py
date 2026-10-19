"""引擎运行时安装单测：冻结模式下必须在进程内下载。

背景（v1.0.2 实际故障）：便携包内 sys.executable 是 FableTech.exe，
执行 [sys.executable, "-m", "camoufox", "fetch"] 只会再启动一个 GUI，
浏览器永远下载不下来。本测试锁定「冻结模式不得走该子进程路径」。
"""

import subprocess
import sys

import pytest

from fabletech.core.engines.camoufox_engine import CamoufoxEngine


def test_ensure_runtime_frozen_uses_inprocess_fetcher(monkeypatch):
    """冻结模式：调用 CamoufoxFetcher().install()，不得 spawn FableTech.exe。"""
    eng = CamoufoxEngine()
    monkeypatch.setattr(CamoufoxEngine, "available",
                        lambda self: (False, "浏览器未下载"))

    calls = {"install": 0, "subprocess": 0}

    class FakeFetcher:
        def install(self, replace: bool = False) -> None:
            calls["install"] += 1

    import camoufox.pkgman as pm
    monkeypatch.setattr(pm, "CamoufoxFetcher", FakeFetcher)
    monkeypatch.setattr(pm, "installed_verstr", lambda: "156.0.1-beta.36")

    real_run = subprocess.run
    banned = []

    def spy_run(args, *a, **kw):
        # 只盯「用 sys.executable 调 -m camoufox/pip」这一故障模式
        # （沙箱自身的 node/trash 等子进程不计）
        try:
            cmd = [str(x) for x in args]
        except TypeError:
            cmd = []
        if cmd and cmd[0] == str(sys.executable) and any(
                m in cmd for m in ("camoufox", "pip", "patchright")):
            banned.append(cmd)
        return real_run(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy_run)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert eng.ensure_runtime() is True
    assert calls["install"] == 1, "未调用进程内 CamoufoxFetcher.install()"
    assert not banned, (
        f"冻结模式不应 spawn {sys.executable} -m …（会重启 GUI）：{banned}")


def test_ensure_runtime_already_available_short_circuits(monkeypatch):
    eng = CamoufoxEngine()
    monkeypatch.setattr(CamoufoxEngine, "available", lambda self: (True, ""))
    called = {"n": 0}
    import camoufox.pkgman as pm

    class FakeFetcher:
        def install(self, replace: bool = False) -> None:
            called["n"] += 1

    monkeypatch.setattr(pm, "CamoufoxFetcher", FakeFetcher)
    assert eng.ensure_runtime() is True
    assert called["n"] == 0


def test_ensure_runtime_download_failure_graceful(monkeypatch):
    eng = CamoufoxEngine()
    monkeypatch.setattr(CamoufoxEngine, "available",
                        lambda self: (False, "浏览器未下载"))

    class BrokenFetcher:
        def install(self, replace: bool = False) -> None:
            raise RuntimeError("网络不可达")

    import camoufox.pkgman as pm
    monkeypatch.setattr(pm, "CamoufoxFetcher", BrokenFetcher)
    assert eng.ensure_runtime() is False   # 不抛异常，返回 False
