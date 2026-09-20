"""周期性在线巡检：并发探测全部主机，间隔由 ONLINE_CHECK_INTERVAL 控制。

在容器中作为独立进程运行（compose 的 checker 服务）。
"""
from __future__ import annotations

import asyncio
import time

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand

from hosts.models import Host
from hosts.probe import probe_host_async


class Command(BaseCommand):
    help = "周期性探测所有主机的在线状态（SSH 真实登录 / RDP 握手）"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="只跑一轮后退出")
        parser.add_argument("--interval", type=int,
                            default=settings.ONLINE_CHECK_INTERVAL)

    def handle(self, *args, **options):
        if not settings.ONLINE_CHECK_ENABLED and not options["once"]:
            self.stdout.write("在线探测已关闭（ONLINE_CHECK_ENABLED=false）")
            return

        interval = options["interval"]
        self.stdout.write(self.style.SUCCESS(
            f"在线巡检启动，间隔 {interval}s，超时 {settings.ONLINE_CHECK_TIMEOUT}s"
        ))
        while True:
            started = time.monotonic()
            async_to_sync(self._run_round)()
            if options["once"]:
                return
            elapsed = time.monotonic() - started
            time.sleep(max(1.0, interval - elapsed))

    async def _run_round(self) -> None:
        host_ids = await sync_to_async(
            lambda: list(Host.objects.values_list("pk", flat=True))
        )()
        if not host_ids:
            return
        # 每轮最多 32 并发
        semaphore = asyncio.Semaphore(32)

        async def guarded(hid: int):
            async with semaphore:
                try:
                    result = await probe_host_async(hid)
                    self.stdout.write(
                        f"  host#{hid} {result['name']}: {result['status']}"
                    )
                except Exception as exc:  # noqa: BLE001
                    self.stderr.write(f"  host#{hid} 探测异常: {exc}")

        await asyncio.gather(*(guarded(hid) for hid in host_ids))
