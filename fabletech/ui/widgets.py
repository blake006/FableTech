"""通用 UI 组件：弹窗、确认、表格、主密码对话框。

所有组件基于 CustomTkinter；tkinter 不可用时（无显示环境）导入本模块仍安全，
仅实例化窗口会失败。
"""

from __future__ import annotations

import customtkinter as ctk

from .theme import (
    BG_PANEL, DANGER, FONT, FONT_SMALL, PRIMARY, TEXT_MAIN, TEXT_MUTED,
    WARNING,
)


def apply_theme() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")


def show_error(parent, title: str, message: str) -> None:
    _dialog(parent, title, message, color=DANGER)


def show_info(parent, title: str, message: str) -> None:
    _dialog(parent, title, message, color=PRIMARY)


def show_warning(parent, title: str, message: str) -> None:
    _dialog(parent, title, message, color=WARNING)


def ask_confirm(parent, title: str, message: str) -> bool:
    """确认对话框。True=确认。"""
    result = {"ok": False}

    win = ctk.CTkToplevel(parent)
    win.title(title)
    win.geometry("380x180")
    win.transient(parent)
    win.grab_set()
    ctk.CTkLabel(win, text=message, font=FONT, wraplength=340,
                 justify="left").pack(pady=(24, 16), padx=20)
    row = ctk.CTkFrame(win, fg_color="transparent")
    row.pack(pady=8)

    def _ok():
        result["ok"] = True
        win.destroy()

    ctk.CTkButton(row, text="确认", width=90, fg_color=PRIMARY,
                  command=_ok).pack(side="left", padx=8)
    ctk.CTkButton(row, text="取消", width=90, fg_color=BG_PANEL,
                  command=win.destroy).pack(side="left", padx=8)
    parent.wait_window(win)
    return result["ok"]


def ask_master_password(parent, title: str = "主密码", is_create: bool = False) -> str | None:
    """主密码输入对话框。取消返回 None。"""
    result: dict = {"pw": None}

    win = ctk.CTkToplevel(parent)
    win.title(title)
    win.geometry("420x240")
    win.transient(parent)
    win.grab_set()
    ctk.CTkLabel(
        win,
        text=("首次使用：请设置主密码（至少 8 位）。主密码用于加密所有档案数据，"
              "丢失后数据无法恢复！" if is_create else
              "请输入主密码解锁数据（所有档案数据经 AES-256-GCM 加密）。"),
        font=FONT_SMALL, wraplength=380, justify="left",
    ).pack(pady=(20, 12), padx=20)
    pw1 = ctk.CTkEntry(win, width=360, show="•", placeholder_text="主密码")
    pw1.pack(pady=6)
    pw2 = None
    if is_create:
        pw2 = ctk.CTkEntry(win, width=360, show="•", placeholder_text="再次输入主密码")
        pw2.pack(pady=6)

    def _submit():
        v = pw1.get()
        if is_create:
            if pw2 is None or v != pw2.get():
                show_error(win, "错误", "两次输入的主密码不一致")
                return
            if len(v) < 8:
                show_error(win, "错误", "主密码至少 8 位")
                return
        result["pw"] = v
        win.destroy()

    ctk.CTkButton(win, text="确定", fg_color=PRIMARY,
                  command=_submit).pack(pady=12)
    parent.wait_window(win)
    return result["pw"]


def _dialog(parent, title: str, message: str, color: str) -> None:
    win = ctk.CTkToplevel(parent)
    win.title(title)
    win.geometry("460x220")
    win.transient(parent)
    win.grab_set()
    bar = ctk.CTkFrame(win, height=4, fg_color=color)
    bar.pack(fill="x")
    ctk.CTkLabel(win, text=message, font=FONT, wraplength=420,
                 justify="left").pack(pady=(28, 16), padx=24)
    ctk.CTkButton(win, text="知道了", width=100, fg_color=color,
                  command=win.destroy).pack(pady=8)


class PageFrame(ctk.CTkFrame):
    """页面基类：标题 + 内容区。"""

    title: str = "页面"

    def __init__(self, master, app=None, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.app = app
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(16, 8))
        ctk.CTkLabel(header, text=self.title, font=("Microsoft YaHei UI", 18, "bold"),
                     text_color=TEXT_MAIN).pack(side="left")
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=20, pady=(4, 16))
        self.build()

    def build(self) -> None:
        """子类实现页面内容。"""

    def on_show(self) -> None:
        """页面切换可见时刷新（子类可覆写）。"""


def section(parent, text: str) -> ctk.CTkFrame:
    """带标题的分区块。"""
    frame = ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=10)
    ctk.CTkLabel(frame, text=text, font=("Microsoft YaHei UI", 13, "bold"),
                 text_color=TEXT_MAIN).pack(anchor="w", padx=16, pady=(12, 4))
    return frame
