"""仪表盘与作业接口。

- ``GET /api/dashboard``  在线主机数 / 主机总数 / 今日作业数 / 失败数 / 最近操作
- ``GET /api/jobs``       作业列表
- ``GET /api/jobs/{id}/output``  从 Redis 读取作业输出缓冲
- ``POST /api/jobs/run``  在一台 Linux 主机上经 asyncssh 跑一条命令
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from hosts.models import Host, HostStatus

from .models import AuditLog, Job, JobStatus
from .services import log_action


class DashboardView(APIView):
    def get(self, request):
        # 以平台时区（Asia/Shanghai）的“今天”为准统计作业
        import datetime as dt

        day_start = timezone.make_aware(
            dt.datetime.combine(timezone.localdate(), dt.time.min)
        )
        jobs_today = Job.objects.filter(created_at__gte=day_start)
        data = {
            "online_hosts": Host.objects.filter(status=HostStatus.ONLINE).count(),
            "total_hosts": Host.objects.count(),
            "jobs_today": jobs_today.count(),
            "jobs_failed_today": jobs_today.filter(status=JobStatus.FAILED).count(),
            "recent_audits": list(
                AuditLog.objects.values(
                    "id", "actor", "action", "target_type", "target_name",
                    "detail", "result", "created_at",
                )[:15]
            ),
            "generated_at": timezone.now().isoformat(),
        }
        return Response(data)


class JobViewSet(viewsets.ViewSet):
    def list(self, request):
        jobs = Job.objects.all()[:50]
        return Response([
            {
                "id": j.pk,
                "name": j.name,
                "host_id": j.host_id,
                "host_name": j.host_name_snapshot,
                "command": j.command,
                "status": j.status,
                "exit_code": j.exit_code,
                "created_at": j.created_at,
                "finished_at": j.finished_at,
            }
            for j in jobs
        ])

    @action(detail=True, methods=["get"])
    def output(self, request, pk=None):
        job = Job.objects.filter(pk=pk).first()
        if not job:
            return Response({"detail": "作业不存在"}, status=404)
        # 已结束且已归档：读数据库；执行中：读 Redis 实时缓冲
        if job.status != JobStatus.RUNNING and job.output:
            output = job.output
        else:
            try:
                from .services import decode_output_entries, get_redis

                raw = get_redis().lrange(job.stream_key, 0, -1)
                output, _ = decode_output_entries(raw)
            except Exception:  # noqa: BLE001
                output = job.output or ""
        return Response({"id": job.pk, "output": output})

    @action(detail=False, methods=["post"])
    def run(self, request):
        """在指定 Linux 主机上执行命令（asyncssh），输出实时写入 Redis 并广播。"""
        from hosts.models import OS

        from .quick import run_legacy_job_on_worker

        host = Host.objects.filter(pk=(request.data or {}).get("host_id")).first()
        command = (request.data or {}).get("command", "")
        name = (request.data or {}).get("name") or "手动作业"
        if not host:
            return Response({"detail": "host_id 无效"}, status=400)
        if host.os_type == OS.WINDOWS:
            return Response(
                {"detail": f"主机「{host.name}」是 Windows 机器，命令执行仅支持 Linux/SSH"},
                status=400,
            )
        if not command:
            return Response({"detail": "command 不能为空"}, status=400)

        job = Job.objects.create(
            name=name, host=host, host_name_snapshot=host.name, command=command,
            status=JobStatus.RUNNING, started_at=timezone.now(),
        )
        log_action("run", "job", name, detail=f"主机「{host.name}」: {command[:80]}")
        try:
            result = run_legacy_job_on_worker(job.pk, host.pk, command)
        except Exception as exc:  # noqa: BLE001
            result = {"status": JobStatus.FAILED, "exit_code": None,
                      "error": str(exc)}
        return Response({"job_id": job.pk, **result}, status=201)
