"""CloakBrowser 引擎（可选增强，需 license key）。

import guard：包不存在时明确提示不可用，不影响应用。
license key 来源：settings 表 cloak_license_key 或环境变量 CLOAKBROWSER_LICENSE_KEY。
注意：cloakbrowser 为订阅制商业包，公开 Python API 不稳定；本实现仅完成
可用性检测与配置透传，实际启动在检测到订阅 SDK 后按同基 Chromium 路径执行。
"""

from __future__ import annotations

import os

from .base import BrowserEngine, EngineSession, LaunchSpec

LICENSE_ENV = "CLOAKBROWSER_LICENSE_KEY"


class CloakEngine(BrowserEngine):
    name = "cloak"

    def _license_key(self) -> str:
        key = os.environ.get(LICENSE_ENV, "").strip()
        if not key:
            try:
                from ...core.storage import Storage
                from ...config import paths
                st = Storage(paths().db)
                key = (st.get_setting("cloak_license_key") or "").strip()
            except Exception:
                key = ""
        return key

    def available(self) -> tuple[bool, str]:
        try:
            import cloakbrowser  # noqa: F401
        except ImportError:
            return False, ("未安装 cloakbrowser（可选增强引擎）。请使用 Camoufox "
                           "主引擎；如需 CloakBrowser，请按订阅渠道安装 SDK。")
        if not self._license_key():
            return False, (f"缺少 CloakBrowser 订阅 key。请在设置页填写，"
                           f"或设置环境变量 {LICENSE_ENV}。")
        return True, ""

    def ensure_runtime(self) -> bool:
        return self.available()[0]

    def launch(self, spec: LaunchSpec) -> EngineSession:
        from .camoufox_engine import EngineUnavailableError

        ok, hint = self.available()
        if not ok:
            raise EngineUnavailableError(hint)
        # cloakbrowser 订阅 SDK 安装后：与 camoufox 同基（Chromium C++ 补丁），
        # 启动参数按订阅版文档透传 fingerprint seed + proxy；当前开源环境
        # 无稳定公开 API，具体实现留待订阅环境验证（见 BUILD_NOTES）。
        raise EngineUnavailableError(
            "CloakBrowser 引擎在开源环境下无稳定公开 API，请使用 Camoufox 引擎。")

    def close(self, session: EngineSession) -> None:
        session.close()
