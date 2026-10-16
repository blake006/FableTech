"""引擎注册表与工厂。"""

from __future__ import annotations

from .base import BrowserEngine, EngineSession, LaunchSpec
from .camoufox_engine import CamoufoxEngine, EngineUnavailableError
from .chromium_engine import ChromiumEngine
from .cloak_engine import CloakEngine

ENGINES: dict[str, type[BrowserEngine]] = {
    CamoufoxEngine.name: CamoufoxEngine,
    CloakEngine.name: CloakEngine,
    ChromiumEngine.name: ChromiumEngine,
}


def get_engine(name: str) -> BrowserEngine:
    cls = ENGINES.get(name)
    if cls is None:
        raise EngineUnavailableError(
            f"未知引擎「{name}」。可用引擎：{'、'.join(ENGINES)}")
    return cls()


def engine_status() -> list[tuple[str, bool, str]]:
    """所有引擎可用性列表 [(name, available, hint)]，供设置页展示。"""
    out = []
    for name, cls in ENGINES.items():
        eng = cls()
        ok, hint = eng.available()
        out.append((name, ok, hint))
    return out


__all__ = [
    "BrowserEngine", "LaunchSpec", "EngineSession", "EngineUnavailableError",
    "CamoufoxEngine", "CloakEngine", "ChromiumEngine",
    "ENGINES", "get_engine", "engine_status",
]
