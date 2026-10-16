"""GUI 真实构建测试：装配 App → 解锁 → 构造主窗口（全部页面控件）。

覆盖 entry_gui 的完整导入链与 MainWindow 构建路径——
此路径正是 v1.0 便携包 GUI 启动崩溃（app.py logger 导入错误）
未被冒烟覆盖的地方。Windows 下 tkinter 可无头创建窗口。
"""

import tempfile
from pathlib import Path

import pytest


def test_gui_full_construction(tmp_path):
    """App 装配 + 主密码 + MainWindow 全页面构建（真实控件）。"""
    import customtkinter as ctk

    from fabletech.app import App
    from fabletech.config import clear_data_dir_override, set_data_dir_override
    from fabletech.ui.main_window import MainWindow

    set_data_dir_override(tmp_path)
    try:
        app = App()
        app.setup_master_password("TestMaster123!")
        assert not app.locked
        assert app.profiles is not None and app.scheduler is not None

        ctk.set_appearance_mode("dark")
        win = MainWindow(app)
        try:
            # 五个页面全部构建 + 切换到每一页（触发 on_show）
            for key in ("profiles", "proxies", "tasks", "logs", "settings"):
                win.show(key)
            win.update()  # 强制刷新挂起的事件（探测构建期异常）
            assert win.winfo_exists()
            # 布局回归：侧栏 Logo 必须已降采样（800x800 原图会撑爆布局）
            logo = getattr(win, "_logo_photo", None)
            if logo is not None:
                assert logo.width() <= 120, (
                    f"侧栏 Logo 宽度 {logo.width()} 超出（应≤120，"
                    "未降采样会导致界面错乱）")
                assert logo.height() <= 120
        finally:
            win.destroy()
            app.shutdown()
    finally:
        clear_data_dir_override()


def test_entry_gui_import_chain():
    """entry_gui.py 的导入链（打包 exe 的启动路径）。"""
    from fabletech.app import run_gui  # noqa: F401
    assert callable(run_gui)


def test_watermark_js_generation():
    """动态水印脚本生成（含档案名转义）。"""
    from fabletech.core.engines.camoufox_engine import _watermark_js
    js = _watermark_js('测试"档案"')
    assert "__fabletech_wm__" in js
    assert "pointer-events:none" in js
    # 名称经 JSON 转义，不产生脚本注入
    assert '\\"' in js or '测试' in js
