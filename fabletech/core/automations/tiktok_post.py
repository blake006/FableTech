"""TikTok 发视频 Recipe（上传流程，选择器集中常量）。

params: video_path（本地视频文件，必填）, caption（文案，可选）
"""

from __future__ import annotations

import secrets
from pathlib import Path

from ..logger import log_event
from .base import Recipe, RecipeContext, Step

TIKTOK_UPLOAD = "https://www.tiktok.com/tiktokstudio/upload"

# 选择器集中常量（站点改版只改这里）
SELECTORS = {
    "file_input": 'input[type="file"]',
    "caption": '[contenteditable="true"], div[data-contents="true"]',
    "post_button": 'button:has-text("Post"), button:has-text("发布")',
    "progress": '[role="progressbar"], .progress-bar',
    "uploaded_ok": 'text="Uploaded", text="上传成功"',
}


async def _human_type(page, text: str) -> None:
    for ch in text:
        await page.keyboard.type(ch, delay=60 + secrets.randbelow(150))


class TikTokPostRecipe(Recipe):
    name = "tiktok_post"
    title = "TikTok 发视频"

    def steps(self, ctx: RecipeContext) -> list[Step]:
        return [
            Step("校验视频文件", self._check_video),
            Step("打开上传页", self._open_upload),
            Step("上传视频文件", self._upload_file),
            Step("填写文案", self._fill_caption),
            Step("人工：确认并发布",
                 None, requires_human=True,
                 human_prompt="请在浏览器中确认视频预览与设置，点击「发布」后回到 FableTech 点击「继续」。"),
            Step("等待上传完成", self._wait_upload),
        ]

    async def _check_video(self, ctx: RecipeContext) -> None:
        from .base import RecipeError
        path = ctx.params.get("video_path")
        if not path:
            raise RecipeError("缺少 params.video_path（本地视频文件路径）")
        p = Path(path)
        if not p.exists():
            raise RecipeError(f"视频文件不存在：{path}")
        if p.suffix.lower() not in (".mp4", ".mov", ".webm", ".avi", ".mkv"):
            raise RecipeError(f"不支持的视频格式：{p.suffix}")
        ctx.data["video_path"] = str(p.resolve())
        ctx.data["video_size"] = p.stat().st_size

    async def _open_upload(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        await page.goto(TIKTOK_UPLOAD, wait_until="domcontentloaded",
                        timeout=60000)
        # 未登录会被重定向到登录页
        if "login" in page.url:
            from .base import RecipeError
            raise RecipeError("未登录 TikTok，请先在该档案登录后再发帖")

    async def _upload_file(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        await page.wait_for_selector(SELECTORS["file_input"], timeout=30000,
                                     state="attached")
        await page.set_input_files(SELECTORS["file_input"],
                                   ctx.data["video_path"])

    async def _fill_caption(self, ctx: RecipeContext) -> None:
        caption = (ctx.params.get("caption") or "").strip()
        if not caption:
            return
        page = self.expect_page(ctx.session)
        try:
            box = await page.wait_for_selector(SELECTORS["caption"],
                                               timeout=20000)
            await box.click()
            await _human_type(page, caption)
        except Exception:
            log_event("WARNING", "recipe.tiktok_post",
                      "文案填写失败（选择器失效），请人工补填",
                      profile_id=ctx.profile_id)

    async def _wait_upload(self, ctx: RecipeContext) -> None:
        page = self.expect_page(ctx.session)
        # 等进度条消失或出现完成标记
        try:
            await page.wait_for_selector(SELECTORS["progress"],
                                         state="hidden", timeout=180000)
        except Exception:
            pass
        ctx.data["upload_finished"] = True
