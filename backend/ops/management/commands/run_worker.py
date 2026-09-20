"""快捷命令异步执行进程。

- 用 ``BLPOP`` 阻塞消费 Redis 队列 ``zhiyue:jobs:queue``；
- 每个作业 id 取出后经 :func:`hosts.runner.run_ssh_job` 在 asyncssh 上执行，
  单进程内用 asyncio 并发跑多台主机（信号量限并发）；
- 启动时把上一次进程残留的“执行中”作业标记为「执行服务重启」失败，
  避免它们永远挂在“执行中”。

compose 中以独立 ``runner`` 服务运行（复用后端镜像）。
"""
from __future__ import annotations

import asyncio
import signal

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from ops.models import ErrorKind, Job, JobStatus
from ops.services import JOB_QUEUE_KEY, clear_abort
from zhiyue.events import publish


@sync_to_async
def _load_job(job_id: int):
    return Job.objects.filter(pk=job_id).first()


@sync_to_async
def _reap_stale_jobs() -> int:
    stale = list(Job.objects.filter(status=JobStatus.RUNNING).values_list("id", flat=True))
    if stale:
        Job.objects.filter(id__in=stale).update(
            status=JobStatus.FAILED,
            error_kind=ErrorKind.WORKER_RESTART,
            error_message="执行服务在作业运行期间重启，该作业未跑完，请重新执行。",
            finished_at=timezone.now(),
        )
        for job_id in stale:
            publish("job.finished", {
                "id": job_id, "status": JobStatus.FAILED,
                "exit_code": None, "error_kind": ErrorKind.WORKER_RESTART,
                "error_message": "执行服务重启，作业中断。",
            })
    return len(stale)


class Command(BaseCommand):
    help = "消费快捷命令作业队列，经 asyncssh 并发执行（长期运行进程）"

    def add_arguments(self, parser):
        parser.add_argument("--concurrency", type=int, default=16,
                            help="单机进程内最多同时执行的作业（主机）数")
        parser.add_argument("--once", action="store_true",
                            help="把当前队列里的作业跑完后退出（便于测试）")

    def handle(self, *args, **options):
        async_to_sync(self._run)(options["concurrency"], options["once"])

    async def _run(self, concurrency: int, once: bool) -> None:
        import redis.asyncio as aioredis

        reaped = await _reap_stale_jobs()
        if reaped:
            self.stdout.write(self.style.WARNING(
                f"[runner] 已把 {reaped} 条上次残留的执行中作业标记为失败（服务重启）"
            ))

        client = aioredis.from_url(
            settings.REDIS_URL, socket_timeout=30, socket_connect_timeout=5
        )
        semaphore = asyncio.Semaphore(concurrency)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass

        in_flight: set[asyncio.Task] = set()
        self.stdout.write(self.style.SUCCESS(
            f"[runner] 已启动，队列 {JOB_QUEUE_KEY}，并发上限 {concurrency}"
        ))

        async def worker(job_id: int) -> None:
            async with semaphore:
                job = await _load_job(job_id)
                if job is None:
                    return
                if job.status != JobStatus.RUNNING:
                    # 入队后被取消/删除等情况
                    return
                self.stdout.write(f"[runner] job#{job_id} 开始 -> {job.host_name_snapshot}")
                try:
                    from hosts.runner import run_ssh_job

                    result = await run_ssh_job(job.pk, job.host_id, job.command)
                    self.stdout.write(
                        f"[runner] job#{job_id} 结束 -> {result.get('status')}"
                    )
                except Exception as exc:  # noqa: BLE001 - 单作业异常不拖垮进程
                    self.stderr.write(f"[runner] job#{job_id} 执行器异常: {exc}")
                finally:
                    try:
                        clear_abort(job_id)
                    except Exception:  # noqa: BLE001
                        pass

        try:
            while not stop.is_set():
                try:
                    popped = await client.blpop(JOB_QUEUE_KEY, timeout=2)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - Redis 短暂不可用
                    self.stderr.write(f"[runner] 读取队列失败，1s 后重试: {exc}")
                    await asyncio.sleep(1)
                    continue
                if popped is None:
                    if once:
                        break
                    continue
                _key, raw_id = popped
                try:
                    job_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                task = asyncio.create_task(worker(job_id))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
                if once:
                    # --once：继续取到队列空为止，最后统一等待在飞任务
                    pass
        finally:
            if in_flight:
                self.stdout.write(f"[runner] 等待 {len(in_flight)} 个在飞作业结束…")
                await asyncio.gather(*in_flight, return_exceptions=True)
            await client.aclose()
            self.stdout.write("[runner] 已退出")
