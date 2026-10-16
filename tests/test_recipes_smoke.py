"""Recipe 步骤机冒烟测试：mock EngineSession，验证
running → waiting_human → resume → done 状态流转（不启动真实浏览器）。"""

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from fabletech.core.automations import get_recipe
from fabletech.core.automations.base import (
    AutoHumanController,
    RecipeContext,
    RecipeError,
    Step,
)


class FakeSession:
    """模拟 EngineSession：在专属 loop 线程跑协程（与真实实现同构）。"""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.page = MagicMock()
        self.context = MagicMock()
        self.calls: list[str] = []

    def run_sync(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=10)


def make_recipe_with_human():
    """构造带人工介入点的测试配方。"""
    from fabletech.core.automations.base import Recipe

    class DemoRecipe(Recipe):
        name = "demo"
        title = "测试配方"

        def steps(self, ctx):
            async def step_a(ctx):
                ctx.data["a"] = 1

            async def step_b(ctx):
                ctx.data["b"] = ctx.data["a"] + 1

            return [
                Step("步骤A", step_a),
                Step("人工介入点", None, requires_human=True,
                     human_prompt="请人工完成某事"),
                Step("步骤B", step_b),
            ]

    return DemoRecipe()


def test_state_machine_running_waiting_done():
    session = FakeSession()
    statuses: list[str] = []
    ctrl = AutoHumanController()
    ctx = RecipeContext(session=session, params={}, profile_id="p1")
    result = make_recipe_with_human().run(
        session, ctx, controller=ctrl, on_status=statuses.append)

    assert result.success is True
    assert statuses == ["running", "waiting_human", "running", "done"]
    # 人工提示被通知过
    assert ctrl.prompts == ["请人工完成某事"]
    # 步骤数据传递
    assert ctx.data == {"a": 1, "b": 2}


def test_step_failure_marks_failed():
    session = FakeSession()

    from fabletech.core.automations.base import Recipe

    class FailRecipe(Recipe):
        name = "fail"
        title = "失败配方"

        def steps(self, ctx):
            async def boom(ctx):
                raise RecipeError("步骤炸了")
            return [Step("爆炸步骤", boom)]

    statuses: list[str] = []
    result = FailRecipe().run(session, RecipeContext(session=session),
                              controller=AutoHumanController(),
                              on_status=statuses.append)
    assert result.success is False
    assert "步骤炸了" in result.error
    assert statuses == ["running", "failed"]


def test_real_recipe_outlook_has_two_human_checkpoints():
    r = get_recipe("outlook_register")
    session = FakeSession()
    ctx = RecipeContext(session=session, params={
        "first_name": "Test", "last_name": "User", "password": "Aa1!aaaa"})
    steps = r.steps(ctx)
    human_steps = [s for s in steps if s.requires_human]
    assert len(human_steps) == 2
    assert "FunCaptcha" in human_steps[0].human_prompt
    assert "短信" in human_steps[1].human_prompt


def test_real_recipe_x_post_validates_text():
    r = get_recipe("x_post")
    session = FakeSession()
    ctx = RecipeContext(session=session, params={"text": "hi"})
    # _type_text 抛错（无 text 时）
    import pytest as _pt
    ctx2 = RecipeContext(session=session, params={})
    with _pt.raises(RecipeError):
        # session.page 是 MagicMock，wait_for_selector 返回 awaitable 需手动处理；
        # 这里直接调用内部校验分支（params 无 text 会在 _human_type 前抛错）
        session.run_sync(r._type_text(ctx2))


def test_recipe_registry_complete():
    from fabletech.core.automations import RECIPES, recipe_titles
    assert set(RECIPES) == {"outlook_register", "x_post", "tiktok_post",
                            "health_check"}
    titles = dict(recipe_titles())
    assert titles["outlook_register"].startswith("Outlook")
