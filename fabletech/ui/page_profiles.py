"""档案管理页：列表 + 新建/编辑 + 启动/关闭（引擎启动放子线程，UI 不阻塞）。"""

from __future__ import annotations

import threading

import customtkinter as ctk

from ..core.engines import engine_status
from ..core.engines.camoufox_engine import EngineUnavailableError
from .widgets import PageFrame, ask_confirm, show_error, show_info, show_warning

STATUS_CN = {"active": "活跃", "exception": "异常", "banned": "封禁"}
ENGINE_CN = {"camoufox": "Camoufox（主）", "cloak": "CloakBrowser",
             "chromium": "Patchright-Chromium"}


class ProfilesPage(PageFrame):
    title = "档案管理"

    def build(self) -> None:
        # 工具栏
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(bar, text="＋ 新建档案", width=120,
                      command=self._create_dialog).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="启动", width=80,
                      fg_color="#46A758", hover_color="#3A8A4A",
                      command=self._launch).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="关闭", width=80,
                      command=self._close).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="保存登录态", width=110,
                      command=self._save_state).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="删除", width=80, fg_color="#E5484D",
                      hover_color="#C93A3F",
                      command=self._delete).pack(side="left", padx=4)
        self.status_lbl = ctk.CTkLabel(bar, text="", font=("Microsoft YaHei UI", 11))
        self.status_lbl.pack(side="right")

        # 表格（Treeview 滚动）
        import tkinter.ttk as ttk
        columns = ("name", "engine", "proxy", "status", "running", "notes")
        self.tree = ttk.Treeview(self.body, columns=columns, show="headings",
                                 height=18)
        for col, text, w in (("name", "名称", 180), ("engine", "引擎", 160),
                             ("proxy", "代理", 160), ("status", "状态", 70),
                             ("running", "运行中", 70), ("notes", "备注", 160)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w)
        scroll = ctk.CTkScrollbar(self.body, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self._refresh()

    # ---------- 数据 ----------

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if not self.app.profiles:
            return
        running = set(self.app.profiles.running_profiles())
        for p in self.app.profiles.list():
            proxy_txt = "—"
            if p.get("proxy_id"):
                row = self.app.storage.get_proxy(p["proxy_id"])
                if row:
                    from ..core.proxy import ProxyInfo
                    proxy_txt = ProxyInfo(
                        row["type"], row["host"], row["port"],
                        None, None).masked_url()
            self.tree.insert(
                "", "end", iid=p["id"],
                values=(p["name"], ENGINE_CN.get(p["engine"], p["engine"]),
                        proxy_txt, STATUS_CN.get(p["status"], p["status"]),
                        "是" if p["id"] in running else "否", p.get("notes", "")))

    def on_show(self) -> None:
        self._refresh()

    def _selected(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    # ---------- 操作 ----------

    def _create_dialog(self) -> None:
        win = ctk.CTkToplevel(self)
        win.title("新建档案")
        win.geometry("460x400")
        win.transient(self)
        win.grab_set()
        form = ctk.CTkFrame(win, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=20, pady=16)

        entries: dict[str, ctk.CTkEntry] = {}
        for label, key, ph in (("名称", "name", "如：X-账号-01"),
                               ("平台标签", "tags", "x / tiktok / outlook，逗号分隔"),
                               ("分组", "group", "如：推广组A"),
                               ("备注", "notes", "可选")):
            ctk.CTkLabel(form, text=label).pack(anchor="w")
            e = ctk.CTkEntry(form, width=400, placeholder_text=ph)
            e.pack(pady=(0, 8))
            entries[key] = e

        ctk.CTkLabel(form, text="引擎").pack(anchor="w")
        eng_status = {n: ok for n, ok, _ in engine_status()}
        engine_var = ctk.StringVar(value="camoufox")
        seg = ctk.CTkSegmentedButton(
            form, values=[ENGINE_CN[k] for k in ENGINE_CN],
            command=lambda v: engine_var.set(
                next(k for k, cn in ENGINE_CN.items() if cn == v)))
        seg.set(ENGINE_CN["camoufox"])
        seg.pack(pady=(0, 8))
        if not eng_status.get("camoufox", False):
            show_warning(self, "引擎提示", "Camoufox 运行时未就绪，"
                          "请先到「设置 → 引擎运行时」一键安装。")

        def _ok():
            name = entries["name"].get().strip()
            if not name:
                show_error(win, "错误", "请填写档案名称")
                return
            try:
                self.app.profiles.create(
                    name=name, engine=engine_var.get(),
                    platform_tags=entries["tags"].get().strip(),
                    group_name=entries["group"].get().strip(),
                    notes=entries["notes"].get().strip())
            except Exception as e:
                show_error(win, "创建失败", str(e))
                return
            win.destroy()
            self._refresh()

        ctk.CTkButton(win, text="创建", fg_color="#4F6BED",
                      command=_ok).pack(pady=10)

    def _launch(self) -> None:
        pid = self._selected()
        if not pid:
            show_warning(self, "提示", "请先在列表中选择一个档案")
            return
        if self.app.profiles.is_running(pid):
            show_info(self, "提示", "该档案已在运行中")
            return
        self.status_lbl.configure(text="正在启动浏览器…")

        def worker():
            try:
                self.app.profiles.launch(pid)
                self.after(0, lambda: (self.status_lbl.configure(text="已启动"),
                                       self._refresh()))
            except EngineUnavailableError as e:
                # 注意：except 的 e 在块结束即删除，lambda 延迟执行会
                # NameError；必须先取为局部变量再用默认参数绑定
                msg = str(e)
                self.after(0, lambda m=msg: show_error(self, "引擎不可用", m))
            except Exception as e:
                msg = str(e)
                self.after(0, lambda m=msg: show_error(self, "启动失败", m))

        threading.Thread(target=worker, daemon=True).start()

    def _close(self) -> None:
        pid = self._selected()
        if not pid:
            show_warning(self, "提示", "请先选择档案")
            return
        try:
            self.app.profiles.close(pid)
            self._refresh()
        except Exception as e:
            show_error(self, "关闭失败", str(e))

    def _save_state(self) -> None:
        pid = self._selected()
        if not pid:
            show_warning(self, "提示", "请先选择档案")
            return
        try:
            path = self.app.profiles.save_state(pid)
            show_info(self, "已保存", f"登录态已加密存档：{path}")
        except Exception as e:
            show_error(self, "保存失败", str(e))

    def _delete(self) -> None:
        pid = self._selected()
        if not pid:
            show_warning(self, "提示", "请先选择档案")
            return
        if not ask_confirm(self, "确认删除", "删除档案将同时清除其加密 Cookie 与浏览器数据，且不可恢复。确认？"):
            return
        try:
            self.app.profiles.delete(pid)
            self._refresh()
        except Exception as e:
            show_error(self, "删除失败", str(e))
