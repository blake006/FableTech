"""scheduler 冒烟测试：fake launch_fn + health_check 配方，
验证 入队 → running → done 状态流转与 waiting_human 恢复机制。
"""

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from fabletech.config import set_data_dir_override
from fabletech.core.profiles import ProfileService
from fabletech.core.scheduler import BlockingHumanController, Scheduler
from fabletech.core.storage import Storage


class FakeSession:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        # AsyncMock：所有 Playwright 异步方法返回可等待对象
        self.page = AsyncMock()
        self.context = AsyncMock()

    def run_sync(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=10)


@pytest.fixture()
def env(tmp_path):
    set_data_dir_override(tmp_path)
    st = Storage(tmp_path / "db.db")
    yield st, FakeSession
    from fabletech.config import clear_data_dir_override
    clear_data_dir_override()


def _wait_task(st: Storage, tid: str, want: set[str], timeout: float = 15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = st.get_task(tid)
        if t and t["status"] in want:
            return t
        time.sleep(0.1)
    raise AssertionError(f"任务 {tid} 超时未进入 {want}，当前状态="
                         f"{st.get_task(tid) and st.get_task(tid)['status']}")


def test_scheduler_runs_task_to_done(env):
    st, session_cls = env
    launched = []

    def fake_launch(pid):
        s = session_cls()
        launched.append(pid)
        return s

    def fake_close(pid):
        pass

    profiles = MagicMock()
    scheduler = Scheduler(st, launch_fn=fake_launch, close_fn=fake_close,
                          max_concurrency=2)
    pid = "a" * 12
    task = scheduler.submit_nowait("health_check", pid,
                                   params={"platform": "x"})
    t = _wait_task(st, task["id"], {"done", "failed"})
    assert t["status"] == "done"
    assert len(launched) == 1
    scheduler.shutdown()


def test_blocking_controller_waits_until_resume():
    ctrl = BlockingHumanController()
    done = threading.Event()

    def worker():
        ctrl.wait_for_resume()
        done.set()

    threading.Thread(target=worker, daemon=True).start()
    assert not done.wait(timeout=0.3)   # 未恢复 → 阻塞
    ctrl.resume()
    assert done.wait(timeout=2)          # 恢复 → 放行
