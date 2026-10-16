"""指纹自检（P1 预留接口）。

v1：返回 not-implemented 友好提示；v1.1 计划接入 BrowserLeaks/CreepJS 评分。
"""

from __future__ import annotations

NOT_IMPLEMENTED_HINT = (
    "指纹自检（BrowserLeaks / CreepJS 评分）将在 v1.1 提供。"
    "当前版本可手动访问 browserleaks.com 或 creepjs 进行检测。"
)


def run_selfcheck() -> dict:
    """执行指纹自检。当前版本为预留接口。"""
    return {
        "implemented": False,
        "message": NOT_IMPLEMENTED_HINT,
        "items": [],
    }
