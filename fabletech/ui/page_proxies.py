"""代理池页：列表（凭证脱敏）+ 新增 + 批量导入 + 连通测试 + 地理查询。

支持协议：HTTP/HTTPS/SOCKS5（浏览器直连）+ hy2/hysteria2/anytls
（经 mihomo 本地中转为 SOCKS5，首次使用自动下载中转内核）。
"""

from __future__ import annotations

import json
import threading

import customtkinter as ctk

from ..core.proxy import (
    RELAY_TYPES, parse_proxy_bulk, query_geo, suggest_locale,
)
from ..core.proxy import test_proxy as run_proxy_test
from .widgets import PageFrame, show_error, show_info, show_warning

TYPE_CN = {"http": "HTTP", "https": "HTTPS", "socks5": "SOCKS5",
           "socks5h": "SOCKS5h", "socks4": "SOCKS4",
           "hysteria2": "Hysteria2", "anytls": "AnyTLS",
           "vmess": "VMess", "vless": "VLESS", "trojan": "Trojan",
           "ss": "Shadowsocks"}


# 单条新增表单提供的类型（hy2/anytls 请用批量导入粘贴完整 URI）
ADD_TYPES = {"http": "HTTP", "https": "HTTPS", "socks5": "SOCKS5",
             "socks5h": "SOCKS5h", "socks4": "SOCKS4"}


class ProxiesPage(PageFrame):
    title = "代理池"

    def build(self) -> None:
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(bar, text="＋ 新增代理", width=110,
                      command=self._add_dialog).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="批量导入", width=90,
                      command=self._bulk_dialog).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="连通测试", width=90,
                      command=self._test).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="地理查询", width=90,
                      command=self._geo).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="删除", width=80, fg_color="#E5484D",
                      hover_color="#C93A3F",
                      command=self._delete).pack(side="left", padx=4)

        import tkinter.ttk as ttk
        columns = ("type", "addr", "latency", "geo", "enabled", "tags")
        self.tree = ttk.Treeview(self.body, columns=columns, show="headings",
                                 height=16)
        for col, text, w in (("type", "类型", 80), ("addr", "地址（凭证已脱敏）", 300),
                             ("latency", "延迟", 80), ("geo", "地理位置", 140),
                             ("enabled", "启用", 60), ("tags", "标签", 120)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w)
        scroll = ctk.CTkScrollbar(self.body, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self._refresh()

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for row in self.app.storage.list_proxies():
            from ..core.proxy import ProxyInfo
            masked = ProxyInfo(row["type"], row["host"], row["port"],
                               None, None).masked_url()
            latency = f"{row['latency_ms']}ms" if row.get("latency_ms") else "—"
            geo = " ".join(x for x in (row.get("country"), row.get("city")) if x) or "—"
            self.tree.insert("", "end", iid=row["id"],
                             values=(TYPE_CN.get(row["type"], row["type"]),
                                     masked, latency, geo,
                                     "是" if row["enabled"] else "否",
                                     row.get("tags", "")))

    def on_show(self) -> None:
        self._refresh()

    def _selected(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _add_dialog(self) -> None:
        win = ctk.CTkToplevel(self)
        win.title("新增代理")
        win.geometry("440x380")
        win.transient(self)
        win.grab_set()
        form = ctk.CTkFrame(win, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=20, pady=16)

        ctk.CTkLabel(form, text="类型").pack(anchor="w")
        type_var = ctk.StringVar(value="socks5")
        seg = ctk.CTkSegmentedButton(
            form, values=list(ADD_TYPES.values()),
            command=lambda v: type_var.set(
                next(k for k, cn in ADD_TYPES.items() if cn == v)))
        # 注意：CTkSegmentedButton.set() 返回 None，不能链式 .pack()
        # （曾导致「类型」之后的表单全部不渲染）
        seg.set(ADD_TYPES["socks5"])
        seg.pack(pady=(0, 8))
        ctk.CTkLabel(form, text="Hysteria2 / AnyTLS 请用「批量导入」粘贴完整 URI"
                                "（hy2://… / anytls://…）",
                     font=("Microsoft YaHei UI", 10),
                     text_color="#9BA0AA").pack(anchor="w", pady=(0, 8))

        entries = {}
        for label, key, ph in (("地址 host", "host", "1.2.3.4"),
                               ("端口", "port", "1080"),
                               ("用户名（可选）", "user", ""),
                               ("密码（可选，加密存储）", "pass", ""),
                               ("标签", "tags", "美国、住宅")):
            ctk.CTkLabel(form, text=label).pack(anchor="w")
            e = ctk.CTkEntry(form, width=400, placeholder_text=ph,
                             show="•" if key == "pass" else None)
            e.pack(pady=(0, 6))
            entries[key] = e

        def _ok():
            try:
                host = entries["host"].get().strip()
                port = int(entries["port"].get().strip())
                user = entries["user"].get().strip() or None
                pw = entries["pass"].get().strip() or None
                vault = self.app.vault
                self.app.storage.create_proxy(
                    type=type_var.get(), host=host, port=port,
                    username_enc=vault.encrypt(user) if user else None,
                    password_enc=vault.encrypt(pw) if pw else None,
                    tags=entries["tags"].get().strip())
            except ValueError as e:
                show_error(win, "错误", f"端口必须是数字：{e}")
                return
            except Exception as e:
                show_error(win, "错误", str(e))
                return
            win.destroy()
            self._refresh()

        ctk.CTkButton(win, text="保存", command=_ok).pack(pady=8)

    def _bulk_dialog(self) -> None:
        win = ctk.CTkToplevel(self)
        win.title("批量导入代理")
        win.geometry("560x460")
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(win, text="每行一条。支持：\n"
                     "  http/https/socks5://user:pass@host:port（或 host:port，默认 HTTP）\n"
                     "  hy2://auth@host:port/?sni=x&insecure=1&obfs=salamander&obfs-password=y\n"
                     "  hysteria2://… / anytls://password@host:port?sni=x&insecure=1\n"
                     "  vmess://base64 / vless://uuid@host:port?security=reality…\n"
                     "  trojan://password@host:port?sni=x / ss://base64@host:port\n"
                     "  预代理链：任意协议 URI 加 &dialer=<代理ID>（前置一跳）"
                     ).pack(anchor="w", padx=16, pady=(12, 4))
        box = ctk.CTkTextbox(win, width=520, height=280)
        box.pack(padx=16, pady=8)

        def _ok():
            ok_list, bad = parse_proxy_bulk(box.get("1.0", "end"))
            imported = 0
            for p in ok_list:
                vault = self.app.vault
                extra = p.extra_to_dict()
                self.app.storage.create_proxy(
                    type=p.type, host=p.host, port=p.port,
                    username_enc=vault.encrypt(p.username) if p.username else None,
                    password_enc=vault.encrypt(p.password) if p.password else None,
                    extra_enc=(vault.encrypt(json.dumps(extra, ensure_ascii=False))
                               if extra else None))
                imported += 1
            win.destroy()
            msg = f"成功导入 {imported} 条"
            if bad:
                msg += f"；跳过 {len(bad)} 条异常行（如第 {bad[0][0]} 行：{bad[0][2]}）"
            show_info(self, "批量导入", msg)
            self._refresh()

        ctk.CTkButton(win, text="导入", command=_ok).pack(pady=8)

    def _test(self) -> None:
        pid = self._selected()
        if not pid:
            show_warning(self, "提示", "请先选择一条代理")
            return
        row = self.app.storage.get_proxy(pid)
        if not row:
            return

        def worker():
            from ..core.proxy import ProxyInfo
            from ..core.relay import relay_manager
            from ..core.proxy import test_relay_proxy
            from datetime import datetime, timezone
            username = password = extra = None
            if row.get("username_enc"):
                username = self.app.vault.decrypt_str(row["username_enc"].hex())
            if row.get("password_enc"):
                password = self.app.vault.decrypt_str(row["password_enc"].hex())
            if row.get("extra_enc"):
                try:
                    extra = json.loads(
                        self.app.vault.decrypt_str(row["extra_enc"].hex()))
                except Exception:
                    extra = None
            if row["type"] in RELAY_TYPES or extra:
                # 中转型代理：启动本地中转 → 经中转实测 + 出口地理回填
                p = ProxyInfo.from_extra(row["type"], row["host"],
                                         row["port"], username, password, extra)
                try:
                    host, port = relay_manager().endpoint_for(pid, p)
                    ok, info = test_relay_proxy(host, port)
                    if ok:
                        self.app.storage.update_proxy(
                            pid, latency_ms=0,
                            last_check_at=datetime.now(timezone.utc)
                            .isoformat(timespec="seconds"))
                finally:
                    relay_manager().release(pid)
                self.after(0, lambda: (show_info if ok else show_warning)(
                    self, "中转链路测试",
                    f"{p.masked_url()}\n{info}\n（出口地理已可用于档案对齐）"))
                self.after(0, self._refresh)
                return
            p = ProxyInfo(row["type"], row["host"], row["port"],
                          username, password)
            ok, latency, msg = run_proxy_test(p)
            if ok:
                self.app.storage.update_proxy(
                    pid, latency_ms=int(latency or 0),
                    last_check_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            self.after(0, lambda: (show_info if ok else show_warning)(
                self, "连通测试", f"{p.masked_url()}\n{msg}"))
            self.after(0, self._refresh)

        threading.Thread(target=worker, daemon=True).start()

    def _geo(self) -> None:
        pid = self._selected()
        if not pid:
            show_warning(self, "提示", "请先选择一条代理")
            return
        row = self.app.storage.get_proxy(pid)
        if not row:
            return

        def worker():
            relay_type = row["type"] in RELAY_TYPES
            geo = query_geo(row["host"])
            if geo.get("countryCode"):
                self.app.storage.update_proxy(
                    pid, country=geo.get("country", ""),
                    city=geo.get("city", ""))
            note = ""
            if relay_type:
                note = ("\n\n注意：中转型代理显示的是服务器地理，"
                        "非出口地理。\n出口地理请用「连通测试」"
                        "（经本地中转实测并自动回填）。")
            self.after(0, lambda: show_info(
                self, "出口地理",
                f"{geo.get('country', '未知')} {geo.get('city', '')} "
                f"ISP：{geo.get('isp', '—')}\n建议 locale："
                f"{suggest_locale(geo.get('countryCode', ''))}{note}"))
            self.after(0, self._refresh)

        threading.Thread(target=worker, daemon=True).start()

    def _delete(self) -> None:
        pid = self._selected()
        if not pid:
            show_warning(self, "提示", "请先选择一条代理")
            return
        self.app.storage.delete_proxy(pid)
        self._refresh()
