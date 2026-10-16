"""Outlook 注册 Recipe（半自动）。

流程：打开 signup.live.com → 填姓名/生日 → 用户名/密码 → 人工 FunCaptcha
→ 人工短信验证 → URL 跳转检测完成 → save_state 加密存档 → 写 account_meta。

params:
  first_name, last_name, birth_year, birth_month, birth_day,
  username（可选，缺省随机）, password（必填，强密码）
"""

from __future__ import annotations

import re
import secrets
import string

from ..logger import log_event
from ..cookies import CookieVault
from ..storage import Storage
from .base import Recipe, RecipeContext, Step

SIGNUP_URL = "https://signup.live.com/"
DONE_URL_PATTERN = re.compile(r"(priv|inbox|outlook\.live|outlook\.com)")

# 选择器集中常量（站点改版只改这里）
SEL = {
    "first_name": 'input[name="firstName"], input#firstNameInput',
    "last_name": 'input[name="lastName"], input#lastNameInput',
    "next": 'button[type="submit"], button:has-text("Next")',
    "birth_month": 'select[aria-label="Birth month"], select[name="BirthMonth"]',
    "birth_day": 'input[aria-label="Birth day"], input[name="BirthDay"]',
    "birth_year": 'input[aria-label="Birth year"], input[name="BirthYear"]',
    "username": 'input[name="NewUsername"], input#usernameInput',
    "domain_suffix": 'select[name="DomainOptionList"]',
    "password": 'input[name="Password"], input#passwordInput',
}


def strong_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def _fill(page, selector: str, value: str) -> None:
    el = await page.wait_for_selector(selector, timeout=15000)
    await el.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.type(value, delay=80 + secrets.randbelow(120))


async def _click_next(page) -> None:
    btn = await page.wait_for_selector(SEL["next"], timeout=15000)
    await btn.click()


class OutlookRegisterRecipe(Recipe):
    name = "outlook_register"
    title = "Outlook 邮箱注册（半自动）"

    def steps(self, ctx: RecipeContext) -> list[Step]:
        return [
            Step("打开注册页", self._open_signup),
            Step("填写姓名", self._fill_name),
            Step("填写生日", self._fill_birthday),
            Step("选择用户名", self._fill_username),
            Step("设置密码", self._fill_password),
            Step("人工：完成人机验证（FunCaptcha）",
                 None, requires_human=True,
                 human_prompt="请在浏览器中完成 FunCaptcha 人机验证，完成后回到 FableTech 点击「继续」。"),
            Step("人工：完成手机短信验证",
                 None, requires_human=True,
                 human_prompt="请在浏览器中完成手机短信验证（输入收到的验证码），完成后点击「继续」。"),
            Step("检测注册完成并加密存档", self._finish),
        ]

    # ---------- 步骤实现 ----------

    async def _open_signup(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        await page.goto(SIGNUP_URL, wait_until="domcontentloaded",
                        timeout=45000)

    async def _fill_name(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        p = ctx.params
        first = p.get("first_name") or "Alex"
        last = p.get("last_name") or "Chen"
        await _fill(page, SEL["first_name"], first)
        await _fill(page, SEL["last_name"], last)
        await _click_next(page)

    async def _fill_birthday(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        p = ctx.params
        month = p.get("birth_month", "5")
        day = p.get("birth_day", "15")
        year = p.get("birth_year", "1995")
        try:
            await page.select_option(SEL["birth_month"], value=month)
        except Exception:
            pass  # 表单结构变化时跳过，由人工介入点兜底
        await _fill(page, SEL["birth_day"], str(day))
        await _fill(page, SEL["birth_year"], str(year))
        await _click_next(page)

    async def _fill_username(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        p = ctx.params
        username = p.get("username")
        if not username:
            username = (f"{p.get('first_name', 'alex')}."
                        f"{secrets.token_hex(4)}").lower()
            p["username"] = username
        try:
            await _fill(page, SEL["username"], username)
        except Exception:
            pass  # 选择器失效时留给人工
        try:
            await _click_next(page)
        except Exception:
            pass

    async def _fill_password(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        p = ctx.params
        password = p.get("password") or strong_password()
        p["password"] = password
        try:
            await _fill(page, SEL["password"], password)
            await _click_next(page)
        except Exception:
            pass

    async def _finish(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        url = page.url
        if not DONE_URL_PATTERN.search(url):
            # 页面结构可能已改版：不强行判失败，记录现状交人工确认
            ctx.data["completed"] = False
            ctx.data["note"] = f"未检测到完成页（当前 URL：{url}），请人工确认"
            log_event("WARNING", "recipe.outlook_register",
                      ctx.data["note"], profile_id=ctx.profile_id)
            return
        ctx.data["completed"] = True
        # 加密存档 Cookie（storage_state → AES-256-GCM）
        n = 0
        try:
            from ...config import paths
            path = ctx.session.save_state(
                paths().cookies / f"{ctx.profile_id}.enc",
                ctx.vault.encrypt,
            )
            cv = CookieVault(ctx.vault)
            n = len((cv.load(ctx.profile_id) or {}).get("cookies", []))
            ctx.data["state_path"] = str(path)
        except Exception as e:  # 存档失败不致命，记录后继续
            ctx.data["state_error"] = str(e)
            log_event("WARNING", "recipe.outlook_register",
                      f"状态存档失败：{e}", profile_id=ctx.profile_id)
        # 写 account_meta（加密）
        if ctx.storage is not None:
            import json
            from datetime import datetime, timezone
            meta = {
                "platform": "outlook",
                "username": ctx.params.get("username"),
                "password": ctx.params.get("password"),
                "cookie_count": n,
                "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            try:
                enc = ctx.vault.encrypt(json.dumps(meta, ensure_ascii=False))
                ctx.storage.update_profile(ctx.profile_id,
                                           account_meta_enc=enc)
                ctx.data["meta_saved"] = True
            except Exception as e:
                ctx.data["meta_error"] = str(e)
