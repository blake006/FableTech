"""引擎抽象层：BrowserEngine ABC + LaunchSpec + EngineSession。

EngineSession 封装「独立事件循环线程 + Playwright 对象」：
- 每个 session 在专属线程跑 asyncio loop（GUI/CLI 宿主同步调用不阻塞）
- save_state/load_state 经注入的加解密函数处理（与 Vault 解耦）
"""

from __future__ import annotations

import asyncio
import json
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..proxy import ProxyInfo

EncryptFn = Callable[[bytes], bytes]   # bytes → bytes 密文
DecryptFn = Callable[[bytes], bytes]   # bytes 密文 → bytes 明文


@dataclass
class LaunchSpec:
    profile_id: str
    user_data_dir: Path
    proxy: ProxyInfo | None
    fingerprint: dict          # 统一指纹配置
    engine_cfg: dict           # engine_config(fp, engine) 输出
    headless: bool = False
    extra: dict = field(default_factory=dict)


class EngineSession:
    """运行中的浏览器会话。page/context 为标准 Playwright 对象。"""

    def __init__(self, engine_name: str, context: Any, page: Any,
                 loop: asyncio.AbstractEventLoop) -> None:
        self.engine = engine_name
        self.context = context
        self.page = page
        self.cdp_endpoint: str | None = None
        self._loop = loop
        self._closed = False

    def run_sync(self, coro: Awaitable[Any]) -> Any:
        """在 session 的事件循环线程上执行协程并等待结果。"""
        if self._closed:
            raise RuntimeError("会话已关闭")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=120)

    # ---------- 状态存取（storage_state → 加密封存） ----------

    def save_state(self, path: Path, encrypt: EncryptFn) -> Path:
        """导出 context.storage_state → encrypt() 加密 → 落盘。"""
        tmp = path.with_suffix(".tmp.json")
        self.run_sync(self.context.storage_state(path=str(tmp)))
        try:
            plain = tmp.read_bytes()
        finally:
            tmp.unlink(missing_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encrypt(plain))
        return path

    def load_state(self, path: Path, decrypt: DecryptFn) -> int:
        """读取加密状态文件 → 解密 → 注入 Cookie。返回注入条数。"""
        data = json.loads(decrypt(path.read_bytes()).decode("utf-8"))
        cookies = data.get("cookies", [])
        if cookies:
            self.run_sync(self.context.add_cookies(cookies))
        return len(cookies)

    def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run_sync(self.page.screenshot(path=str(path), full_page=False))
        return path

    def goto(self, url: str, wait_until: str = "domcontentloaded",
             timeout_ms: int = 30000) -> None:
        self.run_sync(self.page.goto(url, wait_until=wait_until,
                                     timeout=timeout_ms))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.run_sync(self.context.close())
        except Exception:
            pass
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)


class BrowserEngine(ABC):
    """浏览器引擎抽象。"""

    name: str = "base"

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """(是否可用, 不可用时的中文可操作提示)"""

    @abstractmethod
    def ensure_runtime(self) -> bool:
        """安装运行时（pip + 浏览器二进制）。True=成功。"""

    @abstractmethod
    def launch(self, spec: LaunchSpec) -> EngineSession:
        """启动 headed 窗口，返回会话。失败抛带中文信息的异常。"""

    @abstractmethod
    def close(self, session: EngineSession) -> None:
        """关闭会话（调用 session.close 并做引擎侧清理）。"""


def _run_loop_in_thread() -> asyncio.AbstractEventLoop:
    """启动事件循环线程，返回 loop。"""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True,
                     name=f"fabletech-engine-{id(loop):x}").start()
    return loop
