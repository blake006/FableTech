"""档案服务：CRUD + 启动编排（指纹→代理→Cookie→引擎）+ 运行中档案注册表。

启动编排（对应架构 4.1）：
  读档案(engine/seed/proxy_id) → engine_config(fp, engine)
  → ProxyService 解析 → CookieVault.load → Engine.launch(LaunchSpec)
  → 记录 last_launched_at + 日志
红线：运行中档案不可重复启动（注册表控制）。
"""

from __future__ import annotations

import threading
from pathlib import Path

from ..config import paths, profile_user_data_dir
from .logger import log_event
from .cookies import CookieVault
from .crypto import Vault
from .engines import EngineSession, get_engine
from .fingerprint import engine_config, generate_fingerprint, new_seed
from .proxy import ProxyInfo
from .storage import Storage

PROFILE_STATUSES = {"active", "exception", "banned"}


class ProfileService:
    def __init__(self, storage: Storage, vault: Vault) -> None:
        self.st = storage
        self.vault = vault
        self._running: dict[str, EngineSession] = {}  # profile_id → session
        self._lock = threading.Lock()

    # ---------- CRUD ----------

    def create(self, name: str, engine: str = "camoufox",
               platform_tags: str = "", group_name: str = "",
               proxy_id: str | None = None, notes: str = "",
               seed: str | None = None) -> dict:
        if engine not in ("camoufox", "cloak", "chromium"):
            raise ValueError(f"未知引擎：{engine}")
        return self.st.create_profile(
            name=name, engine=engine, seed=seed or new_seed(),
            platform_tags=platform_tags, group_name=group_name,
            proxy_id=proxy_id, notes=notes,
        )

    def get(self, profile_id: str) -> dict:
        p = self.st.get_profile(profile_id)
        if p is None:
            raise KeyError(f"档案不存在：{profile_id}")
        return p

    def list(self, status: str | None = None) -> list[dict]:
        return self.st.list_profiles(status)

    def update(self, profile_id: str, **fields) -> dict:
        self.st.update_profile(profile_id, **fields)
        return self.get(profile_id)

    def delete(self, profile_id: str) -> None:
        with self._lock:
            if profile_id in self._running:
                raise RuntimeError("档案正在运行，请先关闭再删除")
        self.st.delete_profile(profile_id)
        cv = CookieVault(self.vault)
        cv.delete(profile_id)
        d = profile_user_data_dir(profile_id)
        if d.exists():
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    # ---------- 代理解析 ----------

    def resolve_proxy(self, profile: dict) -> ProxyInfo | None:
        pid = profile.get("proxy_id")
        if not pid:
            return None
        row = self.st.get_proxy(pid)
        if not row:
            return None
        username = password = None
        if row.get("username_enc"):
            username = self.vault.decrypt_str(row["username_enc"].hex())
        if row.get("password_enc"):
            password = self.vault.decrypt_str(row["password_enc"].hex())
        return ProxyInfo(type=row["type"], host=row["host"], port=row["port"],
                         username=username, password=password)

    # ---------- 启动编排 ----------

    def is_running(self, profile_id: str) -> bool:
        with self._lock:
            return profile_id in self._running

    def running_profiles(self) -> list[str]:
        with self._lock:
            return list(self._running)

    def launch(self, profile_id: str, headless: bool = False,
               load_cookies: bool = True) -> EngineSession:
        with self._lock:
            if profile_id in self._running:
                raise RuntimeError("该档案正在运行中，不可重复启动")
        profile = self.get(profile_id)
        engine_name = profile["engine"]
        engine = get_engine(engine_name)

        fp = generate_fingerprint(profile["seed"])
        cfg = engine_config(fp, engine_name)
        # 代理地理 → locale/时区对齐（若有地理信息）
        proxy = self.resolve_proxy(profile)
        geo = self.st.get_proxy(profile["proxy_id"]) if profile.get("proxy_id") else None
        if geo and geo.get("country"):
            from .proxy import suggest_locale
            cfg["locale"] = suggest_locale(geo["country"])

        spec = None
        from .engines.base import LaunchSpec
        spec = LaunchSpec(
            profile_id=profile_id,
            user_data_dir=profile_user_data_dir(profile_id),
            proxy=proxy, fingerprint=fp, engine_cfg=cfg,
            headless=headless,
        )
        session = engine.launch(spec)

        with self._lock:
            self._running[profile_id] = session
        from datetime import datetime, timezone
        self.st.update_profile(
            profile_id,
            last_launched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        log_event("INFO", "profiles",
                  f"档案「{profile['name']}」已通过 {engine_name} 启动",
                  profile_id=profile_id)

        if load_cookies:
            cv = CookieVault(self.vault)
            try:
                n = self._inject_cookies(session, cv, profile_id)
                if n:
                    log_event("INFO", "profiles",
                              f"已注入 {n} 条加密 Cookie",
                              profile_id=profile_id)
            except Exception as e:
                # Cookie 注入失败不应阻断启动
                log_event("WARNING", "profiles",
                          f"Cookie 注入失败（不影响启动）：{e}",
                          profile_id=profile_id)
        return session

    @staticmethod
    def _inject_cookies(session: EngineSession, cv: CookieVault,
                        profile_id: str) -> int:
        loaded = cv.load(profile_id)
        if not loaded:
            return 0
        cookies = loaded.get("cookies", [])
        if cookies:
            session.run_sync(session.context.add_cookies(cookies))
        return len(cookies)

    def save_state(self, profile_id: str, engine: str = "") -> Path:
        """将运行中会话的 storage_state 加密存档。"""
        with self._lock:
            session = self._running.get(profile_id)
        if session is None:
            raise RuntimeError("档案未在运行，无法保存状态")
        p = paths().cookies / f"{profile_id}.enc"
        return session.save_state(p, self.vault.encrypt)

    def close(self, profile_id: str) -> None:
        with self._lock:
            session = self._running.pop(profile_id, None)
        if session is None:
            return
        try:
            engine = get_engine(session.engine)
            engine.close(session)
        except Exception as e:
            log_event("WARNING", "profiles",
                      f"关闭档案时出现异常：{e}", profile_id=profile_id)
        log_event("INFO", "profiles", "档案已关闭", profile_id=profile_id)

    def close_all(self) -> None:
        for pid in self.running_profiles():
            self.close(pid)
