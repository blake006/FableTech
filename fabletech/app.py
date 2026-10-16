"""应用装配：DB 初始化 → 主密码创建/解锁 → 服务装配 → 主窗口。"""

from __future__ import annotations

import sys
from pathlib import Path

from .config import ensure_dirs, is_portable, paths
from .core.cookies import CookieVault
from .core.crypto import Vault, VaultError
from .core.logger import log_event
from .core.profiles import ProfileService
from .core.scheduler import Scheduler
from .core.storage import Storage


class App:
    """应用运行时容器：持有 storage/vault/profiles/scheduler。"""

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir:
            from .config import set_data_dir_override
            set_data_dir_override(data_dir)
        p = ensure_dirs(paths())
        self.paths = p
        self.storage = Storage(p.db)
        self.vault: Vault | None = None
        self.profiles: ProfileService | None = None
        self.scheduler: Scheduler | None = None

    # ---------- 主密码 ----------

    @property
    def locked(self) -> bool:
        return self.vault is None

    def setup_master_password(self, password: str) -> None:
        self.vault = Vault.initialize(self.paths.db, password)
        self._assemble_services()
        log_event("INFO", "app", "已创建主密码并初始化加密仓")

    def unlock(self, password: str) -> None:
        self.vault = Vault.unlock(self.paths.db, password)
        self._assemble_services()
        log_event("INFO", "app", "主密码校验通过，应用已解锁")

    def _assemble_services(self) -> None:
        assert self.vault is not None
        self.profiles = ProfileService(self.storage, self.vault)
        self.scheduler = Scheduler(
            self.storage,
            launch_fn=self.profiles.launch,
            vault=self.vault,
            close_fn=self.profiles.close,
        )

    # ---------- 信息 ----------

    def data_dir_display(self) -> str:
        mode = "便携模式" if is_portable() else "安装模式"
        return f"{mode} · 数据目录：{self.paths.root}"

    def shutdown(self) -> None:
        if self.profiles:
            self.profiles.close_all()
        # 回收可能残留的本地中转进程（hy2/anytls）
        try:
            from .core.relay import relay_manager
            relay_manager().stop_all()
        except Exception:
            pass


def run_gui() -> int:
    """GUI 入口（首次启动主密码创建 / 后续解锁 → 主窗口）。

    环境变量 FABLETECH_MASTER_PASSWORD：设置后跳过对话框直接
    创建/解锁（与 CLI 一致，供自动化/无人值守场景；注意环境变量
    对本机其他进程可见，仅在受控环境使用）。
    """
    app = App()
    if app.locked:
        import customtkinter as ctk
        import os
        from .ui.widgets import (
            apply_theme, ask_master_password, show_error,
        )
        apply_theme()
        env_pw = os.environ.get("FABLETECH_MASTER_PASSWORD")
        if env_pw:
            try:
                if Vault.is_initialized(app.paths.db):
                    app.unlock(env_pw)
                else:
                    app.setup_master_password(env_pw)
            except VaultError as e:
                print(f"FABLETECH_MASTER_PASSWORD 无效：{e}", file=sys.stderr)
                return 2
        else:
            root = ctk.CTk()
            root.withdraw()
            if Vault.is_initialized(app.paths.db):
                while True:
                    pw = ask_master_password(root, "解锁 FableTech")
                    if pw is None:
                        root.destroy()
                        return 1
                    try:
                        app.unlock(pw)
                        break
                    except VaultError as e:
                        show_error(root, "解锁失败", str(e))
            else:
                pw = ask_master_password(root, "设置主密码", is_create=True)
                if pw is None:
                    root.destroy()
                    return 1
                app.setup_master_password(pw)
            root.destroy()

    from .ui.main_window import MainWindow
    win = MainWindow(app)
    try:
        win.mainloop()
    finally:
        app.shutdown()
    return 0
