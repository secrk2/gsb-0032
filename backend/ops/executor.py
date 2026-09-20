"""快捷命令的后台执行调度。

HTTP 请求不能被 SSH 执行阻塞（一台机器超时就是 8 秒、多台更久），因此
维护一个进程内常驻的事件循环线程：``submit`` 把协程丢进去跑，接口立即
返回批次/作业 id，前端靠 SSE 追输出和最终状态。

多 uvicorn worker 下：
- 负载均衡决定 run 请求落到哪个 worker，该 worker 负责执行本批作业；
- 取消请求落到别的 worker 也没关系——取消写 Redis 标志，执行方轮询。
"""
from __future__ import annotations

import asyncio
import threading

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()
_ready = threading.Event()


def _runner() -> None:
    global _loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _loop = loop
    _ready.set()
    loop.run_forever()


def get_loop() -> asyncio.AbstractEventLoop:
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _ready.clear()
            thread = threading.Thread(
                target=_runner, name="zhiyue-quick-runner", daemon=True
            )
            thread.start()
            _ready.wait(timeout=5)
    assert _loop is not None
    return _loop


def submit(coro) -> None:
    """fire-and-forget 提交一个协程到后台事件循环。"""
    asyncio.run_coroutine_threadsafe(coro, get_loop())


async def _mark_crashed(job_id: int, host_id, exc: Exception) -> None:
    """execute_on_host 自身之外的未预期异常：作业收尾为失败而不是无限执行中。"""
    from asgiref.sync import sync_to_async
    from django.utils import timezone

    from hosts.runner import _publish_finished
    from ops.models import ErrorCategory, Job, JobStatus

    message = f"执行器异常：{type(exc).__name__}: {exc}"

    def _write():
        from django.db import connection

        try:
            Job.objects.filter(pk=job_id).update(
                status=JobStatus.FAILED, exit_code=None,
                error_category=ErrorCategory.UNKNOWN, error_message=message,
                finished_at=timezone.now(),
            )
        finally:
            connection.close()

    await sync_to_async(_write)()
    _publish_finished(job_id, host_id, None, {
        "status": JobStatus.FAILED, "exit_code": None,
        "error_category": ErrorCategory.UNKNOWN, "error_message": message,
        "interrupted": False,
    })


async def run_batch(batch_id: int, targets: list[tuple[int, int]], command: str) -> None:
    """并发执行一个批次的全部作业，结束后写汇总操作记录。

    ``targets`` 为 ``[(job_id, host_id), ...]``。
    """
    from asgiref.sync import sync_to_async

    from hosts.runner import execute_on_host
    from ops.models import Job, JobStatus
    from ops.services import log_action

    async def _one(job_id: int, host_id: int):
        try:
            return await execute_on_host(job_id, host_id, command)
        except Exception as exc:  # noqa: BLE001 - 兜底：绝不让作业永远停在执行中
            await _mark_crashed(job_id, host_id, exc)
            return {"status": "failed", "exit_code": None, "error_category": "unknown"}

    results = await asyncio.gather(*[
        _one(job_id, host_id) for job_id, host_id in targets
    ])

    def _summarize():
        from django.db import connection

        from ops.models import CommandBatch

        try:
            batch = CommandBatch.objects.filter(pk=batch_id).first()
            jobs = list(Job.objects.filter(batch_id=batch_id))
            ok = sum(1 for j in jobs if j.status == JobStatus.SUCCESS)
            failed = [j for j in jobs if j.status == JobStatus.FAILED]
            interrupted = sum(1 for j in failed if j.interrupted)
            actor = batch.actor if batch else "admin"
            cmd = (batch.command if batch else command)
            detail = (
                f"批量命令结束：共 {len(jobs)} 台，成功 {ok} 台，"
                f"失败 {len(failed)} 台（其中中断 {interrupted} 台）｜ {cmd[:120]}"
            )
            log_action(
                "quick_run_finish", "command", f"批次 #{batch_id}",
                detail=detail,
                result="success" if not failed else "failed",
                actor=actor,
            )
        finally:
            connection.close()

    await sync_to_async(_summarize)()
