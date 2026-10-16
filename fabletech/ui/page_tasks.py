"""任务调度页：任务列表（waiting_human 高亮）+ 新建任务 + 继续/删除。"""

from __future__ import annotations

import json

import customtkinter as ctk

from ..core.automations import recipe_titles
from .widgets import PageFrame, show_error, show_info, show_warning

STATUS_CN = {"pending": "等待中", "running": "执行中", "waiting_human": "待人工介入",
             "done": "已完成", "failed": "失败"}
STATUS_COLOR = {"waiting_human": "#F5A524", "failed": "#E5484D",
                "done": "#46A758", "running": "#4F6BED"}


class TasksPage(PageFrame):
    title = "任务调度"

    def build(self) -> None:
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(bar, text="＋ 新建任务", width=110,
                      command=self._create_dialog).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="▶ 继续（人工完成后）", width=160,
                      fg_color="#F5A524", hover_color="#D48F1D",
                      command=self._resume).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="删除", width=80, fg_color="#E5484D",
                      hover_color="#C93A3F",
                      command=self._delete).pack(side="left", padx=4)
        ctk.CTkLabel(bar, text="提示：等待人工介入的任务会高亮为黄色，"
                     "人工完成后点「继续」",
                     font=("Microsoft YaHei UI", 11)).pack(side="right")

        import tkinter.ttk as ttk
        columns = ("recipe", "profile", "status", "attempts", "error")
        self.tree = ttk.Treeview(self.body, columns=columns, show="headings",
                                 height=16)
        for col, text, w in (("recipe", "配方", 180), ("profile", "档案", 180),
                             ("status", "状态", 110), ("attempts", "尝试", 60),
                             ("error", "最近错误", 300)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w)
        scroll = ctk.CTkScrollbar(self.body, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self._refresh()
        # 定时刷新（waiting_human 高亮可见）
        self._timer = self.after(3000, self._tick)

    def _tick(self) -> None:
        try:
            self._refresh()
        finally:
            self._timer = self.after(3000, self._tick)

    def _refresh(self) -> None:
        if not self.tree.winfo_exists():
            return
        self.tree.delete(*self.tree.get_children())
        for t in self.app.storage.list_tasks():
            profile_name = "—"
            if t.get("profile_id"):
                p = self.app.storage.get_profile(t["profile_id"])
                if p:
                    profile_name = p["name"]
            title = dict(recipe_titles()).get(t["recipe"], t["recipe"])
            self.tree.insert("", "end", iid=t["id"],
                             values=(title, profile_name,
                                     STATUS_CN.get(t["status"], t["status"]),
                                     t.get("attempts", 0),
                                     (t.get("last_error") or "")[:120]))

    def on_show(self) -> None:
        self._refresh()

    def _selected(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _create_dialog(self) -> None:
        win = ctk.CTkToplevel(self)
        win.title("新建任务")
        win.geometry("480x460")
        win.transient(self)
        win.grab_set()
        form = ctk.CTkFrame(win, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=20, pady=16)

        ctk.CTkLabel(form, text="配方").pack(anchor="w")
        recipe_var = ctk.StringVar(value="outlook_register")
        names = [t for _, t in recipe_titles()]
        keys = [n for n, _ in recipe_titles()]
        ctk.CTkSegmentedButton(
            form, values=names,
            command=lambda v: recipe_var.set(
                keys[names.index(v)])).set(names[0]).pack(pady=(0, 10))

        ctk.CTkLabel(form, text="档案").pack(anchor="w")
        profiles = self.app.profiles.list() if self.app.profiles else []
        profile_map = {p["name"]: p["id"] for p in profiles}
        pid_var = ctk.StringVar(value=next(iter(profile_map), ""))
        ctk.CTkOptionMenu(form, values=list(profile_map) or ["（无档案）"],
                          variable=pid_var).pack(pady=(0, 10))

        ctk.CTkLabel(form, text="参数（JSON，如发帖内容 {\"text\": \"...\"}）"
                     ).pack(anchor="w")
        params_box = ctk.CTkTextbox(form, width=430, height=140)
        params_box.pack(pady=(0, 10))

        def _ok():
            try:
                params = json.loads(params_box.get("1.0", "end").strip() or "{}")
            except ValueError as e:
                show_error(win, "参数错误", f"params 不是合法 JSON：{e}")
                return
            pid = profile_map.get(pid_var.get())
            if not pid:
                show_error(win, "错误", "请先创建档案")
                return
            try:
                self.app.scheduler.submit_nowait(recipe_var.get(), pid, params)
            except Exception as e:
                show_error(win, "创建失败", str(e))
                return
            win.destroy()
            self._refresh()

        ctk.CTkButton(win, text="创建并执行", command=_ok).pack(pady=8)

    def _resume(self) -> None:
        tid = self._selected()
        if not tid:
            show_warning(self, "提示", "请先选择任务")
            return
        task = self.app.storage.get_task(tid)
        if not task or task["status"] != "waiting_human":
            show_info(self, "提示", "只有「待人工介入」状态的任务可以继续")
            return
        self.app.scheduler.resume(tid)
        show_info(self, "已恢复", "任务已恢复执行")
        self._refresh()

    def _delete(self) -> None:
        tid = self._selected()
        if not tid:
            show_warning(self, "提示", "请先选择任务")
            return
        self.app.storage.delete_task(tid)
        self._refresh()
