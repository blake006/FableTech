"""最终冒烟：camoufox 引擎 启动 → 打开测试页 → 截图 → 关闭。

验证引擎完整链路：指纹配置 → 浏览器启动 → 页面渲染 → 截图落盘。
"""
import asyncio
from pathlib import Path

from camoufox import AsyncCamoufox

OUT = Path("data/screenshots")
OUT.mkdir(parents=True, exist_ok=True)


async def main() -> None:
    async with AsyncCamoufox(headless=True, humanize=False) as browser:
        page = await browser.new_page()
        await page.goto("https://example.com", timeout=45000)
        title = await page.title()
        ua = await page.evaluate("navigator.userAgent")
        print("page title:", title)
        print("UA:", ua[:80])
        shot = OUT / "smoke-example.png"
        await page.screenshot(path=str(shot))
        print("screenshot:", shot, shot.stat().st_size, "bytes")
        await page.close()
    print("SMOKE OK")


asyncio.run(main())
