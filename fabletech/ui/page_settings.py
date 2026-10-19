"""设置页：数据目录显示、引擎可用性检测、一键安装运行时、CloakBrowser key、关于与合规。"""

from __future__ import annotations

import threading

import customtkinter as ctk

from ..core.engines import engine_status
from .assets import LOGO_PNG, asset_path, has_logo
from .widgets import PageFrame, show_error, show_info, show_warning

ENGINE_CN = {"camoufox": "Camoufox（主引擎，免费）",
             "cloak": "CloakBrowser（可选，需订阅 key）",
             "chromium": "Patchright-Chromium（备选，指纹能力受限）"}

COMPLIANCE_TEXT = (
    "合规与使用约定\n\n"
    "1. 本工具仅供管理团队自有账号的营销运营；不得用于欺诈、垃圾信息、"
    "黄赌毒或规避金融/政府风控。\n"
    "2. 各社媒平台对多账号有明确限制，请控制同 IP 段账号密度与养号节奏。\n"
    "3. 请使用正规代理商的住宅/移动 IP，不从事攻击或爬取受保护数据。\n"
    "4. 主密码丢失 = 数据不可恢复（设计如此），请妥善保管。\n"
    "5. CloakBrowser 仅限团队自用订阅场景，不二次分发、不预装进对外分发包。\n"
    "6. 本应用无遥测、无自动更新；网络外发仅：用户显式触发的 ip-api 地理查询、"
    "引擎运行时下载、以及自动化访问的目标站点。"
)


class SettingsPage(PageFrame):
    title = "设置"

    def build(self) -> None:
        # 数据目录
        dir_card = ctk.CTkFrame(self.body, fg_color="#2A2C33", corner_radius=10)
        dir_card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(dir_card, text="数据目录", font=("Microsoft YaHei UI", 13, "bold")
                     ).pack(anchor="w", padx=16, pady=(12, 4))
        ctk.CTkLabel(dir_card, text=self.app.data_dir_display(),
                     wraplength=880, justify="left").pack(anchor="w",
                                                         padx=16, pady=(0, 12))

        # 引擎运行时
        eng_card = ctk.CTkFrame(self.body, fg_color="#2A2C33", corner_radius=10)
        eng_card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(eng_card, text="引擎运行时",
                     font=("Microsoft YaHei UI", 13, "bold")).pack(
            anchor="w", padx=16, pady=(12, 4))
        self.engine_labels: dict[str, ctk.CTkLabel] = {}
        for name, ok, hint in engine_status():
            row = ctk.CTkFrame(eng_card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=(2, 8))
            mark = "●" if ok else "○"
            color = "#46A758" if ok else "#F5A524"
            # 两行布局：名称一行、提示一行（避免窄窗口下横向挤压截断）
            ctk.CTkLabel(row, text=f"{mark} {ENGINE_CN.get(name, name)}",
                         text_color=color, anchor="w",
                         font=("Microsoft YaHei UI", 12, "bold")
                         ).pack(anchor="w")
            lbl = ctk.CTkLabel(row, text="可用" if ok else hint,
                              justify="left", anchor="w",
                              wraplength=820, text_color="#9BA0AA")
            lbl.pack(anchor="w", fill="x", padx=(18, 0))
            self.engine_labels[name] = lbl
        btn_row = ctk.CTkFrame(eng_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(4, 4))
        ctk.CTkButton(btn_row, text="一键安装运行时",
                      command=self._install_runtime).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="重新检测",
                      command=self._recheck_engines).pack(side="left", padx=4)
        # 下载代理：camoufox 内核约 1.3GB，国内直连 GitHub 常失败，
        # 可复用本机已有代理（如 Clash 的 http://127.0.0.1:7890）
        dl_row = ctk.CTkFrame(eng_card, fg_color="transparent")
        dl_row.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(dl_row, text="下载代理（可选）",
                     text_color="#9BA0AA").pack(side="left")
        self.dl_proxy_entry = ctk.CTkEntry(
            dl_row, width=300,
            placeholder_text="http://127.0.0.1:7890（留空=直连）")
        saved = self.app.storage.get_setting("engine_download_proxy", "") or ""
        if saved:
            self.dl_proxy_entry.insert(0, saved)
        self.dl_proxy_entry.pack(side="left", padx=8)
        ctk.CTkButton(dl_row, text="保存", width=70,
                      command=self._save_dl_proxy).pack(side="left")

        # 反检测增强（GeekezBrowser 借鉴）
        adv_card = ctk.CTkFrame(self.body, fg_color="#2A2C33", corner_radius=10)
        adv_card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(adv_card, text="反检测增强",
                     font=("Microsoft YaHei UI", 13, "bold")).pack(
            anchor="w", padx=16, pady=(12, 4))
        self.webrtc_var = ctk.StringVar(
            value="1" if self.app.storage.get_setting("webrtc_block", "1") != "0"
            else "0")
        ctk.CTkCheckBox(
            adv_card, text="WebRTC 硬屏蔽（阻断本地 IP 泄漏路径，建议开启）",
            variable=self.webrtc_var, onvalue="1", offvalue="0",
            command=self._save_adv_settings).pack(anchor="w", padx=16, pady=2)
        self.watermark_var = ctk.StringVar(
            value="1" if self.app.storage.get_setting("watermark", "1") != "0"
            else "0")
        ctk.CTkCheckBox(
            adv_card, text="动态水印（页面右上角显示档案名，防多窗口误操作）",
            variable=self.watermark_var, onvalue="1", offvalue="0",
            command=self._save_adv_settings).pack(anchor="w", padx=16, pady=(2, 12))

        # CloakBrowser key
        cloak_card = ctk.CTkFrame(self.body, fg_color="#2A2C33", corner_radius=10)
        cloak_card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(cloak_card, text="CloakBrowser 订阅 key（可选）",
                     font=("Microsoft YaHei UI", 13, "bold")).pack(
            anchor="w", padx=16, pady=(12, 4))
        row = ctk.CTkFrame(cloak_card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 12))
        self.cloak_entry = ctk.CTkEntry(row, width=380, show="•",
                                        placeholder_text="或设置环境变量 CLOAKBROWSER_LICENSE_KEY")
        self.cloak_entry.pack(side="left")
        ctk.CTkButton(row, text="保存", width=80,
                      command=self._save_cloak_key).pack(side="left", padx=8)

        # 关于与合规
        about_card = ctk.CTkFrame(self.body, fg_color="#2A2C33", corner_radius=10)
        about_card.pack(fill="both", expand=True)
        ctk.CTkLabel(about_card, text="关于 FableTech",
                     font=("Microsoft YaHei UI", 13, "bold")).pack(
            anchor="w", padx=16, pady=(12, 4))
        # 品牌 Logo（关于页展示原图）
        if has_logo():
            import tkinter as _tk
            self._about_logo = _tk.PhotoImage(
                file=str(asset_path(LOGO_PNG)))
            # 缩放到 96px 展示（tk PhotoImage 的 subsample）
            scale = max(1, self._about_logo.width() // 96)
            small = self._about_logo.subsample(scale, scale)
            ctk.CTkLabel(about_card, image=small, text="").pack(
                anchor="w", padx=16, pady=(4, 8))
        ctk.CTkLabel(about_card,
                     text="FableTech Browser v1.0 · 面向中国出海团队的免费自托管指纹浏览器\n"
                          "数据不出本机 · AES-256-GCM 加密 · 引擎可插拔",
                     justify="left").pack(anchor="w", padx=16, pady=(0, 8))
        ctk.CTkLabel(about_card, text=COMPLIANCE_TEXT, justify="left",
                     wraplength=880).pack(anchor="w", padx=16, pady=(0, 12))
        ctk.CTkButton(
            about_card, text="指纹自检 → 请到「档案管理」页选中档案后点击",
            command=lambda: show_info(
                self, "指纹自检",
                "指纹自检为档案级操作：\n\n"
                "1. 打开「档案管理」页\n"
                "2. 选中要检测的档案\n"
                "3. 点击工具栏「指纹自检」\n\n"
                "系统会启动该档案的浏览器，采集真实指纹数据并与档案配置"
                "逐项比对（webdriver / UA / 时区 / 语言 / 屏幕 / WebGL / "
                "CPU / 内存 / Canvas），同时访问 browserleaks 与 creepjs "
                "并截图存档，输出评分报告。")).pack(anchor="w", padx=16, pady=(0, 14))

    # ---------- 操作 ----------

    def _save_adv_settings(self) -> None:
        """保存反检测增强开关（WebRTC 硬屏蔽 / 动态水印）。"""
        self.app.storage.set_setting("webrtc_block", self.webrtc_var.get())
        self.app.storage.set_setting("watermark", self.watermark_var.get())

    def _save_dl_proxy(self) -> None:
        """保存引擎下载代理（http/https，留空=直连）。"""
        url = self.dl_proxy_entry.get().strip()
        if url and not url.lower().startswith(("http://", "https://")):
            show_error(self, "格式错误", "代理需为 http:// 或 https:// 开头，"
                                        "例如 http://127.0.0.1:7890")
            return
        self.app.storage.set_setting("engine_download_proxy", url)
        show_info(self, "已保存",
                  "下载代理已保存，下次「一键安装运行时」生效" if url
                  else "已清空下载代理（直连模式）")

    def _install_runtime(self) -> None:
        # 应用下载代理：requests 自动读取 HTTP(S)_PROXY 环境变量
        proxy = (self.app.storage.get_setting("engine_download_proxy", "")
                 or "").strip()
        import os
        if proxy:
            os.environ["HTTP_PROXY"] = proxy
            os.environ["HTTPS_PROXY"] = proxy
            os.environ["http_proxy"] = proxy
            os.environ["https_proxy"] = proxy
        else:
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                os.environ.pop(k, None)

        def worker():
            import sys
            from ..core.engines import get_engine
            results = []
            for name in ("camoufox", "chromium"):
                eng = get_engine(name)
                try:
                    ok = eng.ensure_runtime()
                except Exception as e:
                    ok = False
                results.append(f"{ENGINE_CN[name]}：{'成功' if ok else '失败'}")
            msg = "\n".join(results)
            self.after(0, lambda: (show_info(self, "运行时安装", msg),
                                   self._recheck_engines()))

        threading.Thread(target=worker, daemon=True).start()

    def _recheck_engines(self) -> None:
        for name, ok, hint in engine_status():
            lbl = self.engine_labels.get(name)
            if lbl is not None:
                lbl.configure(text="可用" if ok else hint)

    def _save_cloak_key(self) -> None:
        key = self.cloak_entry.get().strip()
        self.app.storage.set_setting("cloak_license_key", key)
        show_info(self, "已保存", "CloakBrowser 订阅 key 已保存（加密仓保护）")
