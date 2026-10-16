"""任务调度器：队列 + 并发上限（默认 2）+ 指数退避重试（最多 3 次）+ 失败截图
+ waiting_human 状态机（GUI 任务页高亮，人工「继续」）。

状态机：pending → running → done / failed / waiting_human →（resume）running
"""

from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from ..config import paths
from .logger import log_event
from .automations import get_recipe
from .automations.base import HumanController, RecipeContext
from .storage import Storage

MAX_ATTEMPTS = 3
DEFAULT_CONCURRENCY = 2
RETRY_BASE_DELAY = 5  # 秒


class BlockingHumanController(HumanController):
    """阻塞等待人工恢复的控制器（GUI/CLI 通用）。

    wait_for_resume 阻塞在 Event 上，直到 resume() 被调用
    （GUI 任务页「继续」按钮或 CLI 回车后触发）。
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def notify(self, prompt: str) -> None:
        # 提示已由 on_status → 任务状态 waiting_human 透传到 GUI/CLI
        log_event("INFO", "scheduler", f"等待人工介入：{prompt}")

    def wait_for_resume(self) -> None:
        self._event.wait(timeout=3600)  # 最长等 1 小时，防止永久挂起

    def resume(self) -> None:
        self._event.set()


class Scheduler:
    """单例调度器；GUI/CLI 共用。工作线程池执行 Recipe。"""

    def __init__(self, storage: Storage, launch_fn: Callable[[str], object],
                 vault=None, max_concurrency: int = DEFAULT_CONCURRENCY,
                 close_fn: Callable[[str], None] | None = None) -> None:
        self.st = storage
        self._launch_fn = launch_fn      # ProfileService.launch
        self._close_fn = close_fn        # ProfileService.close
        self._vault = vault
        self.max_concurrency = max(1, max_concurrency)
        self._queue: list[str] = []      # task_id FIFO
        self._running: dict[str, threading.Thread] = {}
        self._human_waiting: dict[str, HumanController] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # ---------- 入队 ----------

    def submit(self, task_id: str) -> None:
        with self._lock:
            if task_id not in self._queue and task_id not in self._running:
                self._queue.append(task_id)
                self.st.update_task(task_id, status="pending")
        self._pump()

    def submit_nowait(self, recipe: str, profile_id: str,
                      params: dict | None = None,
                      schedule: str | None = None) -> dict:
        task = self.st.create_task(recipe, profile_id, params, schedule)
        self.submit(task["id"])
        return task

    # ---------- 执行 ----------

    def _pump(self) -> None:
        with self._lock:
            while self._queue and len(self._running) < self.max_concurrency:
                tid = self._queue.pop(0)
                if tid in self._human_waiting:
                    continue  # 等待人工的任务不重复启动
                t = threading.Thread(target=self._worker, args=(tid,),
                                     daemon=True, name=f"fabletech-task-{tid}")
                self._running[tid] = t
                t.start()

    def _worker(self, task_id: str) -> None:
        try:
            self._execute(task_id)
        except Exception as e:
            log_event("ERROR", "scheduler",
                      f"任务 {task_id} 工作线程异常：{e}")
            self.st.update_task(task_id, status="failed",
                                last_error=str(e))
        finally:
            with self._lock:
                self._running.pop(task_id, None)
            self._pump()

    def _execute(self, task_id: str) -> None:
        task = self.st.get_task(task_id)
        if not task or task["status"] in ("done",):
            return
        attempts = task["attempts"] + 1
        self.st.update_task(task_id, status="running", attempts=attempts)

        profile_id = task["profile_id"]
        session = None
        try:
            if not profile_id:
                raise ValueError("任务缺少 profile_id")
            recipe = get_recipe(task["recipe"])
            session = self._launch_fn(profile_id)
            controller = BlockingHumanController()
            with self._lock:
                self._human_waiting[task_id] = controller

            def on_status(s: str) -> None:
                if s == "waiting_human":
                    self.st.update_task(task_id, status="waiting_human")
                elif s == "running" and task_id in self._human_waiting:
                    self.st.update_task(task_id, status="running")

            ctx = RecipeContext(
                session=session,
                params=task_params(task),
                profile=self.st.get_profile(profile_id) or {},
                profile_id=profile_id,
                task_id=task_id,
                vault=self._vault,
                storage=self.st,
            )
            result = recipe.run(session, ctx, controller=controller,
                                on_status=on_status)
            if result.success:
                self.st.update_task(task_id, status="done", last_error=None)
                log_event("INFO", "scheduler", f"任务 {task_id} 完成",
                          profile_id=profile_id)
            else:
                raise RuntimeError(result.error or "配方执行失败")
        except Exception as e:
            shot = self._screenshot(task_id, session)
            with self._lock:
                self._human_waiting.pop(task_id, None)
            if attempts < MAX_ATTEMPTS:
                delay = RETRY_BASE_DELAY * (2 ** (attempts - 1))
                log_event("WARNING", "scheduler",
                          f"任务 {task_id} 第 {attempts} 次失败，{delay}s 后重试：{e}",
                          profile_id=profile_id)
                self.st.update_task(task_id, status="pending",
                                    last_error=str(e))
                threading.Timer(delay, self.submit, args=(task_id,)).start()
            else:
                self.st.update_task(task_id, status="failed",
                                    last_error=f"{e}")
                log_event("ERROR", "scheduler",
                          f"任务 {task_id} 重试 {MAX_ATTEMPTS} 次仍失败：{e}",
                          profile_id=profile_id,
                          screenshot_path=shot)
        finally:
            with self._lock:
                self._human_waiting.pop(task_id, None)
            if self._close_fn is not None and profile_id:
                try:
                    self._close_fn(profile_id)
                except Exception:
                    pass

    def _screenshot(self, task_id: str, session) -> str | None:
        if session is None:
            return None
        try:
            p = paths().screenshots / f"{task_id}.png"
            session.screenshot(p)
            return str(p)
        except Exception:
            return None

    # ---------- waiting_human 恢复 ----------

    def resume(self, task_id: str) -> bool:
        """人工介入完成后恢复任务。True=已受理。"""
        with self._lock:
            ctrl = self._human_waiting.get(task_id)
        task = self.st.get_task(task_id)
        if ctrl is None:
            if task and task["status"] == "waiting_human":
                self.st.update_task(task_id, status="running")
                self.submit(task_id)
                return True
            return False
        self.st.update_task(task_id, status="running")
        ctrl.resume()
        return True

    # ---------- 查询 ----------

    def status(self) -> dict:
        with self._lock:
            return {
                "queued": list(self._queue),
                "running": list(self._running),
                "waiting_human": [tid for tid in self._human_waiting],
                "max_concurrency": self.max_concurrency,
            }

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            self._queue.clear()


def task_params(task: dict) -> dict:
    import json
    try:
        return json.loads(task.get("params") or "{}")
    except (ValueError, TypeError):
        return {}
