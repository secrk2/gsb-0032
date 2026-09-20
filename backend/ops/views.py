"""仪表盘、快捷命令与作业接口。

快捷命令入口（只对 Linux 机器开放）：

- ``POST /api/commands/inspect`` 危险命令预检，返回命中规则及原因说明
- ``POST /api/commands/run``     一次派发到一台或多台 Linux 主机
    * 选到 Windows：整单拒绝（400）并逐台说明原因，不静默执行任何一台；
    * 命中危险命令且未确认：409，要求二次确认；
    * 确认时必须填写确认原因，原因随批次/作业落库并写操作记录；
    * 作业异步入队（``run_worker`` 进程执行），立刻返回 batch + jobs。
- ``GET  /api/jobs``             历史查询：可按 host_id / 状态 / 时间区间过滤
- ``GET  /api/jobs/{id}/output`` 完整输出回放（落库，不截断）
- ``POST /api/jobs/{id}/abort``  中断单机作业
- ``GET  /api/batches`` / ``POST /api/batches/{id}/abort`` 批次查询 / 整批中断
"""
from __future__ import annotations

import asyncio
import datetime as dt

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from hosts.models import Host, HostStatus, OS
from hosts.runner import run_ssh_job

from .danger import inspect_command
from .models import AuditLog, AuditResult, CommandBatch, Job, JobStatus
from .services import enqueue_jobs, log_action, request_abort


# ---------------------------------------------------------------------------
# 序列化小工具
# ---------------------------------------------------------------------------
def _job_payload(j: Job) -> dict:
    return {
        "id": j.pk,
        "batch_id": j.batch_id,
        "name": j.name,
        "host_id": j.host_id,
        "host_name": j.host_name_snapshot,
        "command": j.command,
        "actor": j.actor,
        "status": j.status,
        "exit_code": j.exit_code,
        "error_kind": j.error_kind,
        "error_message": j.error_message,
        "dangerous": j.dangerous,
        "created_at": j.created_at,
        "started_at": j.started_at,
        "finished_at": j.finished_at,
    }


def _parse_iso(value: str):
    """兼容 '2026-09-20' 与完整 ISO 时间；解析失败返回 None。"""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


# ---------------------------------------------------------------------------
# 仪表盘
# ---------------------------------------------------------------------------
class DashboardView(APIView):
    def get(self, request):
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


# ---------------------------------------------------------------------------
# 危险命令预检
# ---------------------------------------------------------------------------
class CommandInspectView(APIView):
    def post(self, request):
        command = (request.data or {}).get("command", "") or ""
        hits = inspect_command(command)
        return Response({
            "dangerous": bool(hits),
            "hits": hits,
        })


# ---------------------------------------------------------------------------
# 快捷命令派发（多机）
# ---------------------------------------------------------------------------
class CommandRunView(APIView):
    def post(self, request):
        data = request.data or {}
        command = (data.get("command") or "").strip()
        host_ids = data.get("host_ids") or []
        name = (data.get("name") or "快捷命令").strip() or "快捷命令"
        actor = (data.get("actor") or "admin").strip() or "admin"
        confirmed = bool(data.get("confirm"))
        confirm_reason = (data.get("confirm_reason") or "").strip()

        if not command:
            return Response({"detail": "command 不能为空"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(host_ids, list) or not host_ids:
            return Response({"detail": "请至少选择一台主机"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            host_ids = [int(hid) for hid in host_ids]
        except (TypeError, ValueError):
            return Response({"detail": "host_ids 必须是整数数组"},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(host_ids) != len(set(host_ids)):
            return Response({"detail": "选中的主机有重复，请去重后再执行"},
                            status=status.HTTP_400_BAD_REQUEST)

        hosts = list(Host.objects.filter(pk__in=host_ids))
        found_ids = {h.pk for h in hosts}
        missing = [hid for hid in host_ids if hid not in found_ids]
        if missing:
            return Response(
                {"detail": f"主机不存在：{', '.join(map(str, missing))}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Windows 硬拦截：逐台说明原因，整单不执行（绝不静默）
        windows = [h for h in hosts if h.os_type != OS.LINUX]
        if windows:
            blocked = [
                {
                    "host_id": h.pk,
                    "name": h.name,
                    "address": h.address,
                    "os_type": h.os_type,
                    "connect_type": h.connect_type,
                    "reason": (
                        f"「{h.name}」是 Windows 主机（{h.connect_type.upper()} 纳管），"
                        "快捷命令执行仅支持 Linux/SSH：平台对 Windows 只做 RDP 在线探测，"
                        "没有可执行 Shell 命令的通道。请取消勾选该主机，或改用 Windows 侧的运维工具。"
                    ),
                }
                for h in windows
            ]
            log_action(
                "run_blocked", "command_batch", name,
                detail=(f"含 {len(blocked)} 台 Windows 主机，已拦截："
                        + "、".join(b["name"] for b in blocked))[:500],
                result=AuditResult.FAILED, actor=actor,
            )
            return Response(
                {
                    "detail": "选中的主机里包含 Windows，快捷命令仅支持 Linux，已整单拦下。",
                    "blocked_windows": blocked,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 危险命令：先确认 + 必填原因，原因留痕
        hits = inspect_command(command)
        if hits:
            if not confirmed:
                return Response(
                    {"detail": "命令命中危险操作规则，需要二次确认。",
                     "dangerous": True, "hits": hits},
                    status=status.HTTP_409_CONFLICT,
                )
            if len(confirm_reason) < 2:
                return Response(
                    {"detail": "执行危险命令必须填写确认原因（至少 2 个字），该原因会随操作留痕。",
                     "dangerous": True, "hits": hits},
                    status=status.HTTP_409_CONFLICT,
                )

        batch = CommandBatch.objects.create(
            name=name[:255],
            command=command,
            actor=actor,
            dangerous=bool(hits),
            confirm_reason=confirm_reason if hits else "",
            host_count=len(hosts),
        )

        jobs = [
            Job(
                batch=batch,
                name=name,
                host=host,
                host_name_snapshot=host.name,
                command=command,
                actor=actor,
                status=JobStatus.RUNNING,
                # started_at 留空，由执行器真正开跑时标记
                dangerous=bool(hits),
                confirm_reason=confirm_reason if hits else "",
            )
            for host in hosts
        ]
        Job.objects.bulk_create(jobs)

        try:
            enqueue_jobs([j.pk for j in jobs])
        except Exception as exc:  # noqa: BLE001 - 队列不可用时给出明确错误
            Job.objects.filter(pk__in=[j.pk for j in jobs]).update(
                status=JobStatus.FAILED,
                error_kind="error",
                error_message=f"执行队列不可用：{exc}",
                finished_at=timezone.now(),
            )
            return Response(
                {"detail": f"命令已记录但派发到执行队列失败：{exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        host_names = "、".join(h.name for h in hosts)
        if hits:
            # AuditLog.detail 限长 512，整体截断避免 MySQL 严格模式报错
            danger_detail = (
                f"危险命令已确认执行，主机「{host_names}」；原因：{confirm_reason}；"
                f"命中：{'/'.join(hit['key'] for hit in hits)}；命令：{command}"
            )[:500]
            log_action(
                "run_dangerous", "command_batch", name,
                detail=danger_detail,
                result=AuditResult.INFO, actor=actor,
            )
        else:
            log_action(
                "run", "command_batch", name,
                detail=f"主机「{host_names}」执行：{command[:200]}"[:500],
                actor=actor,
            )

        return Response(
            {
                "batch_id": batch.pk,
                "dangerous": bool(hits),
                "jobs": [_job_payload(j) for j in jobs],
            },
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# 作业：历史查询 / 输出回放 / 中断
# ---------------------------------------------------------------------------
class JobViewSet(viewsets.ViewSet):
    def list(self, request):
        """作业历史，可按 host_id / status / batch_id / created_at 时间区间过滤。"""
        qs = Job.objects.all()
        query = request.query_params

        host_id = query.get("host_id")
        if host_id:
            try:
                qs = qs.filter(host_id=int(host_id))
            except ValueError:
                return Response({"detail": "host_id 必须是整数"}, status=400)
        batch_id = query.get("batch_id")
        if batch_id:
            try:
                qs = qs.filter(batch_id=int(batch_id))
            except ValueError:
                return Response({"detail": "batch_id 必须是整数"}, status=400)
        job_status = query.get("status")
        if job_status:
            qs = qs.filter(status=job_status)
        date_from = _parse_iso(query.get("from"))
        if date_from:
            qs = qs.filter(created_at__gte=date_from)
        date_to = _parse_iso(query.get("to"))
        if date_to:
            qs = qs.filter(created_at__lte=date_to)

        try:
            limit = min(int(query.get("limit", 100)), 500)
        except ValueError:
            limit = 100
        jobs = list(qs[:limit])
        return Response([_job_payload(j) for j in jobs])

    @action(detail=True, methods=["get"])
    def output(self, request, pk=None):
        job = Job.objects.filter(pk=pk).first()
        if not job:
            return Response({"detail": "作业不存在"}, status=404)
        return Response({
            "id": job.pk,
            "status": job.status,
            "exit_code": job.exit_code,
            "error_kind": job.error_kind,
            "error_message": job.error_message,
            "output": job.output or "",
        })

    @action(detail=True, methods=["post"])
    def abort(self, request, pk=None):
        job = Job.objects.filter(pk=pk).first()
        if not job:
            return Response({"detail": "作业不存在"}, status=404)
        if job.status != JobStatus.RUNNING:
            return Response(
                {"detail": f"作业当前状态为「{job.get_status_display()}」，无需中断。"},
                status=400,
            )
        request_abort(job.pk)
        log_action(
            "abort", "job", job.host_name_snapshot,
            detail=f"中断作业 #{job.pk}：{job.command[:160]}",
            result=AuditResult.INFO,
            actor=(request.data or {}).get("actor") or "admin",
        )
        return Response({"detail": "中断请求已发送", "id": job.pk}, status=202)

    @action(detail=False, methods=["post"])
    def run(self, request):
        """旧接口兼容：在一台 Linux 主机上同步执行命令（新页面请用 /api/commands/run）。"""
        host = Host.objects.filter(pk=(request.data or {}).get("host_id")).first()
        command = ((request.data or {}).get("command") or "").strip()
        name = (request.data or {}).get("name") or "手动作业"
        if not host:
            return Response({"detail": "host_id 无效"}, status=400)
        if host.os_type != OS.LINUX:
            return Response(
                {"detail": f"「{host.name}」是 Windows 主机，快捷命令仅支持 Linux/SSH。"},
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
            result = asyncio.run(run_ssh_job(job.pk, host.pk, command))
        except Exception as exc:  # noqa: BLE001
            result = {"status": JobStatus.FAILED, "exit_code": None,
                      "error_kind": "error", "error": str(exc)}
        return Response({"job_id": job.pk, **result}, status=201)


# ---------------------------------------------------------------------------
# 命令批次：列表 / 整批中断
# ---------------------------------------------------------------------------
class BatchViewSet(viewsets.ViewSet):
    def list(self, request):
        try:
            limit = min(int(request.query_params.get("limit", 50)), 200)
        except ValueError:
            limit = 50
        batches = list(CommandBatch.objects.all()[:limit])
        jobs_by_batch: dict[int, list[Job]] = {}
        for job in Job.objects.filter(batch_id__in=[b.pk for b in batches]):
            jobs_by_batch.setdefault(job.batch_id, []).append(job)
        return Response([
            {
                "id": b.pk,
                "name": b.name,
                "command": b.command,
                "actor": b.actor,
                "dangerous": b.dangerous,
                "confirm_reason": b.confirm_reason,
                "host_count": b.host_count,
                "created_at": b.created_at,
                "jobs": [_job_payload(j) for j in jobs_by_batch.get(b.pk, [])],
            }
            for b in batches
        ])

    @action(detail=True, methods=["post"])
    def abort(self, request, pk=None):
        batch = CommandBatch.objects.filter(pk=pk).first()
        if not batch:
            return Response({"detail": "批次不存在"}, status=404)
        running = list(batch.jobs.filter(status=JobStatus.RUNNING))
        if not running:
            return Response({"detail": "该批次没有执行中的作业。"}, status=400)
        for job in running:
            request_abort(job.pk)
        log_action(
            "abort", "command_batch", batch.name,
            detail=f"整批中断 {len(running)} 台："
                   + "、".join(j.host_name_snapshot for j in running),
            result=AuditResult.INFO,
            actor=(request.data or {}).get("actor") or "admin",
        )
        return Response(
            {"detail": f"已向 {len(running)} 台主机发送中断请求",
             "job_ids": [j.pk for j in running]},
            status=202,
        )
