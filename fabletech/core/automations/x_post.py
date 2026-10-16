"""X (Twitter) 发帖 Recipe。

流程：加载档案 → 登录态校验（头像/个人元素）→ 打开发帖框 → 拟人输入
→ 发布 → 结果校验 → 记录。

params: text（发帖内容，必填）
"""

from __future__ import annotations

import secrets

from ..logger import log_event
from .base import Recipe, RecipeContext, Step

X_HOME = "https://x.com/home"
X_COMPOSE = "https://x.com/compose/post"

# 选择器集中常量（站点改版只改这里）
SEL = {
    "logged_in": '[data-testid="SideNav_AccountSwitcher_Button"],'
                 '[data-testid="AppTabBar_Profile_Link"],'
                 'a[href="/home"][aria-label], [data-testid="primaryColumn"]',
    "compose": '[data-testid="tweetTextarea_0"]',
    "post_button": '[data-testid="tweetButton"],'
                   '[data-testid="tweetButtonInline"]',
    "toast": '[data-testid="toast"]',
}


async def _human_type(page, text: str) -> None:
    """拟人输入：随机节奏逐字符输入。"""
    for ch in text:
        await page.keyboard.type(ch, delay=60 + secrets.randbelow(160))
        if secrets.randbelow(50) == 0:
            await page.wait_for_timeout(300 + secrets.randbelow(500))


class XPostRecipe(Recipe):
    name = "x_post"
    title = "X (Twitter) 发帖"

    def steps(self, ctx: RecipeContext) -> list[Step]:
        return [
            Step("打开首页并校验登录态", self._open_and_check),
            Step("打开发帖框", self._open_composer),
            Step("拟人输入内容", self._type_text),
            Step("发布", self._submit),
            Step("校验发布结果", self._verify),
        ]

    async def _open_and_check(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        await page.goto(X_HOME, wait_until="domcontentloaded",
                        timeout=45000)
        await page.wait_for_selector(SEL["logged_in"], timeout=20000)
        ctx.data["logged_in"] = True

    async def _open_composer(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        # 若当前不在 compose 页，先导航；再聚焦输入框
        if "/compose" not in page.url:
            await page.goto(X_COMPOSE, wait_until="domcontentloaded",
                            timeout=45000)
        await page.wait_for_selector(SEL["compose"], timeout=20000)
        await (await page.wait_for_selector(SEL["compose"])).click()

    async def _type_text(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        text = (ctx.params.get("text") or "").strip()
        if not text:
            from .base import RecipeError
            raise RecipeError("缺少 params.text（发帖内容）")
        if len(text) > 280:
            from .base import RecipeError
            raise RecipeError(f"内容超长（{len(text)} > 280 字符）")
        await _human_type(page, text)
        await page.wait_for_timeout(600)

    async def _submit(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        btn = await page.wait_for_selector(SEL["post_button"], timeout=10000)
        await btn.click()
        await page.wait_for_timeout(3000)

    async def _verify(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        # 发布后 URL 跳回 /home 或出现 toast 视为成功
        ok = "/home" in page.url
        try:
            await page.wait_for_selector(SEL["toast"], timeout=5000)
            ok = True
        except Exception:
            pass
        ctx.data["posted"] = ok
        if not ok:
            log_event("WARNING", "recipe.x_post",
                      "未能自动确认发布结果（无 toast/跳转），请人工核查",
                      profile_id=ctx.profile_id)
