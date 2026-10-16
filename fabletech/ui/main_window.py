"""主窗口：侧边导航 + 页面切换 + 状态栏。"""

from __future__ import annotations

import customtkinter as ctk

from .assets import LOGO_ROUNDED, asset_path, has_logo
from .page_logs import LogsPage
from .page_profiles import ProfilesPage
from .page_proxies import ProxiesPage
from .page_settings import SettingsPage
from .page_tasks import TasksPage
from .theme import (
    BG_DARK, BG_PANEL, FONT, FONT_SMALL, FONT_TITLE, PRIMARY,
    TEXT_MAIN, TEXT_MUTED, WINDOW_SIZE,
)

NAV_ITEMS = [
    ("profiles", "档案管理"),
    ("proxies", "代理池"),
    ("tasks", "任务调度"),
    ("logs", "日志审计"),
    ("settings", "设置"),
]


class MainWindow(ctk.CTk):
    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.title("FableTech Browser v1.0.2")
        self.geometry(WINDOW_SIZE)
        self.minsize(980, 640)
        self._set_window_icon()

        # 布局：侧边导航 | 内容
        self.sidebar = ctk.CTkFrame(self, width=180, corner_radius=0,
                                    fg_color=BG_PANEL)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        # 品牌 Logo（圆角透明版，融入深色侧栏）
        # 注意：tkinter.PhotoImage 按原始像素加载（800x800），必须手动
        # 降采样，否则 800px 图片标签会撑爆 180px 侧栏布局
        if has_logo():
            import tkinter as _tk
            self._logo_photo = _tk.PhotoImage(
                file=str(asset_path(LOGO_ROUNDED)))
            scale = max(1, self._logo_photo.width() // 72)
            self._logo_photo = self._logo_photo.subsample(scale, scale)
            ctk.CTkLabel(self.sidebar, image=self._logo_photo,
                         text="").pack(pady=(20, 6))
        ctk.CTkLabel(self.sidebar, text="FableTech", font=FONT_TITLE,
                     text_color=TEXT_MAIN).pack(pady=(0, 4))
        ctk.CTkLabel(self.sidebar, text="自托管指纹浏览器",
                     font=FONT_SMALL, text_color=TEXT_MUTED).pack(pady=(0, 20))

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        for key, label in NAV_ITEMS:
            btn = ctk.CTkButton(
                self.sidebar, text=label, anchor="w", height=40,
                fg_color="transparent", hover_color=BG_DARK,
                font=FONT, text_color=TEXT_MAIN,
                command=lambda k=key: self.show(k),
            )
            btn.pack(fill="x", padx=12, pady=2)
            self._nav_buttons[key] = btn

        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_DARK)
        self.content.pack(side="right", fill="both", expand=True)

        # 状态栏：左=实时日志（订阅 log_event），右=数据目录/运行中档案
        status_frame = ctk.CTkFrame(self, height=26, corner_radius=0,
                                    fg_color=BG_PANEL)
        status_frame.pack(side="bottom", fill="x")
        status_frame.pack_propagate(False)
        self.log_label = ctk.CTkLabel(
            status_frame, text="就绪", anchor="w", font=FONT_SMALL,
            text_color=TEXT_MUTED)
        self.log_label.pack(side="left", fill="x", expand=True, padx=(12, 8))
        self.statusbar = ctk.CTkLabel(
            status_frame, text="", anchor="e", font=FONT_SMALL,
            text_color=TEXT_MUTED)
        self.statusbar.pack(side="right", padx=(8, 12))
        # 订阅应用日志 → 状态栏实时显示最近一条（线程安全经 after 派发）
        from ..core.logger import add_log_sink, remove_log_sink
        self._log_sink = self._on_log_event
        add_log_sink(self._log_sink)

        self.pages: dict[str, ctk.CTkFrame] = {}
        for key, cls in (("profiles", ProfilesPage), ("proxies", ProxiesPage),
                         ("tasks", TasksPage), ("logs", LogsPage),
                         ("settings", SettingsPage)):
            page = cls(self.content, app=self.app)
            self.pages[key] = page

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show("profiles")
        self._refresh_status()

    def _set_window_icon(self) -> None:
        """设置窗口图标（任务栏/标题栏显示 FableTech Logo）。"""
        if not has_logo():
            return
        try:
            import tkinter as _tk
            self._icon_photo = _tk.PhotoImage(
                file=str(asset_path(LOGO_PNG)))
            self.iconphoto(True, self._icon_photo)
        except Exception:
            pass  # 图标失败不影响主流程

    def _on_log_event(self, level: str, source: str, message: str) -> None:
        """日志订阅回调（可能来自工作线程）→ 派发到 UI 线程更新状态栏。"""
        icon = {"ERROR": "✗", "WARNING": "!", "INFO": "·"}.get(level, "·")
        text = f"{icon} [{source}] {message}"
        try:
            self.after(0, lambda t=text: self.log_label.configure(text=t))
        except Exception:
            pass  # 窗口销毁中的日志忽略

    def _set_log_text(self, text: str) -> None:
        self.log_label.configure(text=text)

    def show(self, key: str) -> None:
        for page in self.pages.values():
            page.pack_forget()
        page = self.pages[key]
        page.pack(fill="both", expand=True)
        if hasattr(page, "on_show"):
            page.on_show()
        for k, btn in self._nav_buttons.items():
            btn.configure(fg_color=PRIMARY if k == key else "transparent")
        self._refresh_status()

    def _refresh_status(self) -> None:
        running = self.app.profiles.running_profiles() if self.app.profiles else []
        self.statusbar.configure(
            text=f"{self.app.data_dir_display()}　|　运行中档案：{len(running)}")

    def _on_close(self) -> None:
        # 注销日志订阅（避免回调到已销毁控件）
        try:
            from ..core.logger import remove_log_sink
            remove_log_sink(self._log_sink)
        except Exception:
            pass
        self.app.shutdown()
        self.destroy()
