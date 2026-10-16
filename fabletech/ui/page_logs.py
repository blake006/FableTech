"""日志页：SQLite logs 表浏览（按级别/档案筛选 + 刷新 + 清理）。"""

from __future__ import annotations

import customtkinter as ctk

from .widgets import PageFrame, show_info

LEVELS = ("ALL", "INFO", "WARNING", "ERROR")


class LogsPage(PageFrame):
    title = "日志审计"

    def build(self) -> None:
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(bar, text="级别").pack(side="left", padx=(4, 4))
        self.level_var = ctk.StringVar(value="ALL")
        ctk.CTkOptionMenu(bar, values=list(LEVELS), variable=self.level_var,
                          command=lambda _: self._refresh(),
                          width=110).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="刷新", width=80,
                      command=self._refresh).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="清理旧日志（保留 5000 条）", width=200,
                      command=self._prune).pack(side="left", padx=4)

        import tkinter.ttk as ttk
        columns = ("ts", "level", "source", "message", "screenshot")
        self.tree = ttk.Treeview(self.body, columns=columns, show="headings",
                                 height=18)
        for col, text, w in (("ts", "时间", 150), ("level", "级别", 70),
                             ("source", "来源", 140), ("message", "消息", 520),
                             ("screenshot", "截图", 160)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w)
        scroll = ctk.CTkScrollbar(self.body, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self._refresh()

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        level = self.level_var.get()
        rows = self.app.storage.list_logs(
            limit=500, level=None if level == "ALL" else level)
        for r in rows:
            self.tree.insert("", "end",
                             values=(r["ts"][:19].replace("T", " "),
                                     r["level"], r["source"],
                                     r["message"][:200],
                                     r.get("screenshot_path") or ""))

    def on_show(self) -> None:
        self._refresh()

    def _prune(self) -> None:
        n = self.app.storage.prune_logs(keep=5000)
        show_info(self, "清理完成", f"已清理 {n} 条旧日志")
        self._refresh()
