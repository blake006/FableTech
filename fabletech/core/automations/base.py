"""Recipe 步骤机：步骤定义 + requires_human 人工介入点 + 回调注入（GUI/CLI 双宿主）。

状态流转（由 scheduler 驱动）：
  pending → running → waiting_human →（人工恢复）running → done / failed
"""

from __future__ import annotations

import inspect
import traceback
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..logger import log_event


# ---------- 数据模型 ----------

@dataclass
class RecipeContext:
    """Recipe 执行上下文。"""
    session: Any = None          # EngineSession
    params: dict = field(default_factory=dict)
    profile: dict = field(default_factory=dict)
    profile_id: str = ""
    task_id: str = ""
    vault: Any = None            # core.crypto.Vault（存档/元数据加密用）
    storage: Any = None          # core.storage.Storage（档案状态更新用）
    data: dict = field(default_factory=dict)   # 步骤间共享数据


@dataclass
class Step:
    name: str
    run: Callable[[RecipeContext], Any] | None
    requires_human: bool = False
    human_prompt: str = ""


@dataclass
class RecipeResult:
    success: bool
    data: dict = field(default_factory=dict)
    error: str = ""
    last_step: str = ""


class HumanController:
    """人工介入控制器（默认 CLI 版：打印提示并等待回车）。

    GUI 宿主传入实现类：弹窗通知 + Event 等待任务页「继续」按钮。
    """

    def notify(self, prompt: str) -> None:
        print(f"\n【需要人工介入】{prompt}\n")

    def wait_for_resume(self) -> None:
        input("人工完成后，按回车继续…")

    def resume(self) -> None:
        """GUI 宿主可调用的恢复入口（默认无需实现）。"""


class AutoHumanController(HumanController):
    """测试用：记录提示并自动恢复。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def notify(self, prompt: str) -> None:
        self.prompts.append(prompt)

    def wait_for_resume(self) -> None:
        pass


class RecipeError(Exception):
    """Recipe 执行失败（步骤抛错）。"""


class Recipe:
    """Recipe 基类。子类实现 name/title/steps()。"""

    name: str = "base"
    title: str = "基础配方"

    def steps(self, ctx: RecipeContext) -> list[Step]:
        raise NotImplementedError

    def run(self, session: Any, ctx: RecipeContext,
            controller: HumanController | None = None,
            on_status: Callable[[str], None] | None = None) -> RecipeResult:
        """执行步骤机。on_status(status) 用于 scheduler 状态流转回调。"""
        controller = controller or HumanController()

        def _status(s: str) -> None:
            if on_status:
                on_status(s)

        result = RecipeResult(success=False)
        _status("running")
        try:
            for step in self.steps(ctx):
                result.last_step = step.name
                log_event("INFO", f"recipe.{self.name}",
                          f"执行步骤：{step.name}", profile_id=ctx.profile_id)
                if step.requires_human:
                    _status("waiting_human")
                    controller.notify(step.human_prompt or step.name)
                    controller.wait_for_resume()
                    _status("running")
                if step.run is not None:
                    ret = step.run(ctx)
                    if inspect.isawaitable(ret):
                        ret = session.run_sync(ret)
                    if isinstance(ret, dict):
                        ctx.data.update(ret)
            result.success = True
            _status("done")
        except Exception as e:
            result.error = f"{type(e).__name__}: {e}"
            result.success = False
            _status("failed")
            log_event("ERROR", f"recipe.{self.name}",
                      f"步骤「{result.last_step}」失败：{result.error}",
                      profile_id=ctx.profile_id)
        return result

    # ---------- 常用工具（子类复用） ----------

    @staticmethod
    def expect_page(session: Any):
        if session is None or session.page is None:
            raise RecipeError("会话未启动，无法执行页面操作")
        return session.page
