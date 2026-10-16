"""账号健康巡检 Recipe：验证登录态/风控提示，更新档案状态。

params: platform（x|tiktok|outlook|facebook|instagram，缺省按档案标签推断）
"""

from __future__ import annotations

from ..logger import log_event
from .base import Recipe, RecipeContext, Step

# 各平台巡检配置（选择器可配置，改版只改这里）
PLATFORM_CONFIG = {
    "x": {
        "home": "https://x.com/home",
        "logged_in": '[data-testid="SideNav_AccountSwitcher_Button"],'
                     '[data-testid="AppTabBar_Profile_Link"]',
        "risk": 'text="Your account is suspended",'
                'text="account suspended",'
                'text="Caution: This account is temporarily restricted"',
    },
    "tiktok": {
        "home": "https://www.tiktok.com/",
        "logged_in": '[data-e2e="profile-icon"], a[href*="/foryou"]',
        "risk": 'text="account banned", text="too many attempts"',
    },
    "outlook": {
        "home": "https://outlook.live.com/mail/0/",
        "logged_in": '[data-testid="PagedList"], button[aria-label*="Account manager"]',
        "risk": 'text="account suspended", text="unusual activity"',
    },
    "facebook": {
        "home": "https://www.facebook.com/",
        "logged_in": '[aria-label="Your profile"], [aria-label="Account"]',
        "risk": 'text="account disabled", text="security check"',
    },
    "instagram": {
        "home": "https://www.instagram.com/",
        "logged_in": 'img[data-testid="user-avatar"], a[href*="/accounts/edit"]',
        "risk": 'text="account disabled", text="suspended"',
    },
}


class HealthCheckRecipe(Recipe):
    name = "health_check"
    title = "账号健康巡检"

    def steps(self, ctx: RecipeContext) -> list[Step]:
        return [
            Step("打开平台首页", self._open_home),
            Step("检测登录态", self._check_login),
            Step("检测风控提示", self._check_risk),
            Step("更新档案状态", self._update_profile),
        ]

    def _platform(self, ctx: RecipeContext) -> str:
        p = (ctx.params.get("platform") or "").lower()
        if p in PLATFORM_CONFIG:
            return p
        tags = (ctx.profile.get("platform_tags") or "").lower()
        for key in PLATFORM_CONFIG:
            if key in tags:
                return key
        raise ValueError(f"无法确定巡检平台（params.platform 或档案标签），"
                         f"可选：{'、'.join(PLATFORM_CONFIG)}")

    async def _open_home(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        cfg = PLATFORM_CONFIG[self._platform(ctx)]
        ctx.data["cfg"] = cfg
        await page.goto(cfg["home"], wait_until="domcontentloaded",
                        timeout=45000)

    async def _check_login(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        cfg = ctx.data["cfg"]
        try:
            await page.wait_for_selector(cfg["logged_in"], timeout=15000)
            ctx.data["logged_in"] = True
        except Exception:
            ctx.data["logged_in"] = False

    async def _check_risk(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        cfg = ctx.data["cfg"]
        risked = False
        try:
            await page.wait_for_selector(cfg["risk"], timeout=3000)
            risked = True
        except Exception:
            pass
        ctx.data["risked"] = risked

    async def _update_profile(self, ctx: RecipeContext) -> None:
        if ctx.storage is None:
            return
        if not ctx.data.get("logged_in"):
            new_status = "exception"   # 登录态失效 → 异常，需人工重验
        elif ctx.data.get("risked"):
            new_status = "banned"      # 检测到风控/封禁提示
        else:
            new_status = "active"
        if new_status != ctx.profile.get("status"):
            ctx.storage.update_profile(ctx.profile_id, status=new_status)
            log_event("INFO", "recipe.health_check",
                      f"档案状态更新为「{new_status}」",
                      profile_id=ctx.profile_id)
        ctx.data["new_status"] = new_status
