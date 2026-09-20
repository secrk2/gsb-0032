"""快捷命令 HTTP 接口。

- ``POST /api/quick/check``   预检：危险规则命中原因 + Windows 机器拦截清单
- ``POST /api/quick/run``     一次向多台 Linux 主机下发同一条命令（202 异步执行）
- ``POST /api/quick/jobs/{id}/cancel``     中断单机执行
- ``POST /api/quick/batches/{id}/cancel``  中断整批
- ``GET  /api/quick/history`` 执行记录，可按主机和时间区间过滤
- ``GET  /api/quick/jobs/{id}/output``     回看某台机器的完整输出
- ``GET  /api/quick/batches/{id}``         批次详情（含每台机器的结果）
"""
from __future__ import annotations

import concurrent.futures

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.views import APIView
from rest_framework.response import Response

from hosts.models import Host, OS
from ops.safety import detect_dangerous

from .executor import get_loop, run_batch, submit
from .models import CommandBatch, Job, JobKind, JobStatus
from .services import get_redis, log_action, publish_cancel

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def _parse_host_ids(data) -> list[int]:
    ids = (data or {}).get("host_ids") or []
    if not isinstance(ids, list):
        raise ValueError("host_ids 必须是数组")
    out = []
    for v in ids:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            raise ValueError(f"非法主机 id: {v!r}")
    if not out:
        raise ValueError("至少选择一台机器")
    return out


def _windows_reason(host: Host) -> str:
    return (
        f"「{host.name}」（{host.address}:{host.port}）是 {host.get_os_type_display()} 机器，"
        f"走 {host.get_connect_type_display()} 通道；快捷命令只支持 Linux/SSH，"
        "Windows 无法执行 Shell 命令，请改选 Linux 机器"
    )


def _serialize_job(job: Job) -> dict:
    return {
        "id": job.pk,
        "batch_id": job.batch_id,
        "host_id": job.host_id,
        "host_name": job.host_name_snapshot,
        "command": job.command,
        "status": job.status,
        "exit_code": job.exit_code,
        "error_category": job.error_category,
        "error_message": job.error_message,
        "interrupted": job.interrupted,
        "has_output": bool(job.output),
        "output_truncated": job.output_truncated,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


class QuickCheckView(APIView):
    def post(self, request):
        data = request.data or {}
        command = data.get("command", "") or ""
        try:
            host_ids = _parse_host_ids(data)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)

        hosts = list(Host.objects.filter(pk__in=host_ids))
        windows = [h for h in hosts if h.os_type == OS.WINDOWS]
        linux = [h for h in hosts if h.os_type != OS.WINDOWS]
        reasons = detect_dangerous(command)
        return Response({
            "dangerous": bool(reasons),
            "reasons": reasons,
            "linux_hosts": [{"id": h.id, "name": h.name} for h in linux],
            "blocked_hosts": [
                {
                    "id": h.id, "name": h.name, "address": h.address,
                    "port": h.port, "os_type": h.os_type,
                    "connect_type": h.connect_type, "reason": _windows_reason(h),
                }
                for h in windows
            ],
        })


class QuickRunView(APIView):
    def post(self, request):
        data = request.data or {}
        command = (data.get("command") or "").strip()
        actor = (data.get("actor") or "admin").strip() or "admin"
        confirm_reason = (data.get("confirm_reason") or "").strip()
        if not command:
            return Response({"detail": "command 不能为空"}, status=400)
        if len(command) > 65535:
            return Response({"detail": "命令过长（上限 64KB）"}, status=400)
        try:
            host_ids = _parse_host_ids(data)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)

        hosts = list(Host.objects.filter(pk__in=host_ids))
        found_ids = {h.pk for h in hosts}
        missing = sorted(set(host_ids) - found_ids)
        if missing:
            return Response({"detail": f"主机不存在: {missing}"}, status=400)

        # 1) Windows 机器：明确拦下并说明原因，不默默跳过
        windows = [h for h in hosts if h.os_type == OS.WINDOWS]
        if windows:
            blocked = [
                {
                    "id": h.id, "name": h.name, "address": h.address,
                    "port": h.port, "os_type": h.os_type,
                    "connect_type": h.connect_type, "reason": _windows_reason(h),
                }
                for h in windows
            ]
            log_action(
                "quick_blocked", "command", "快捷命令",
                detail="选中 Windows 机器被拦截：" + "；".join(b["name"] for b in blocked),
                result="failed", actor=actor,
            )
            return Response(
                {
                    "detail": "选中的机器中包含 Windows 主机，快捷命令入口仅对 Linux（SSH）开放，"
                              "本次未在任何机器上执行",
                    "code": "windows_blocked",
                    "blocked_hosts": blocked,
                },
                status=400,
            )

        # 2) 危险命令：必须二次确认，且确认原因必填并随批次留痕
        reasons = detect_dangerous(command)
        if reasons and not confirm_reason:
            return Response(
                {
                    "detail": "命令命中危险规则，需要二次确认并填写确认原因",
                    "code": "dangerous_confirm_required",
                    "reasons": reasons,
                },
                status=400,
            )

        batch = CommandBatch.objects.create(
            actor=actor,
            command=command,
            host_count=len(hosts),
            dangerous=bool(reasons),
            confirm_reason=confirm_reason if reasons else "",
            confirmed_at=timezone.now() if reasons else None,
        )
        now = timezone.now()
        jobs = [
            Job(
                name=f"快捷命令 @ {h.name}",
                kind=JobKind.QUICK,
                batch=batch,
                host=h,
                host_name_snapshot=h.name,
                command=command,
                status=JobStatus.RUNNING,
                started_at=now,
            )
            for h in hosts
        ]
        Job.objects.bulk_create(jobs)

        if reasons:
            log_action(
                "quick_confirm", "command", f"批次 #{batch.pk}",
                detail=(
                    f"危险命令已确认执行。命中规则：{'；'.join(reasons)}；"
                    f"确认原因：{confirm_reason}；机器："
                    f"{', '.join(h.name for h in hosts)}"
                )[:512],
                result="info", actor=actor,
            )
        log_action(
            "quick_run", "command", f"批次 #{batch.pk}",
            detail=(
                f"向 {len(hosts)} 台机器下发命令：{command[:120]}"
                f"（机器：{', '.join(h.name for h in hosts[:10])}"
                + (f" 等 {len(hosts)} 台" if len(hosts) > 10 else "") + "）"
            )[:512],
            actor=actor,
        )

        targets = [(j.pk, j.host_id) for j in jobs]
        submit(run_batch(batch.pk, targets, command))

        return Response(
            {
                "batch_id": batch.pk,
                "dangerous": bool(reasons),
                "jobs": [_serialize_job(j) for j in jobs],
            },
            status=202,
        )


class JobCancelView(APIView):
    def post(self, request, pk: int):
        job = Job.objects.filter(pk=pk, kind=JobKind.QUICK).first()
        if not job:
            return Response({"detail": "快捷命令执行记录不存在"}, status=404)
        if job.status != JobStatus.RUNNING:
            return Response(
                {"detail": f"执行已结束（{job.status}），无需中断", "status": job.status},
                status=409,
            )
        publish_cancel(job)
        log_action(
            "quick_cancel", "job", job.host_name_snapshot,
            detail=f"请求中断批次 #{job.batch_id} 在该机的执行：{job.command[:120]}",
            result="info",
        )
        return Response({"detail": "已发送中断信号", "job_id": job.pk}, status=202)


class BatchCancelView(APIView):
    def post(self, request, pk: int):
        batch = CommandBatch.objects.filter(pk=pk).first()
        if not batch:
            return Response({"detail": "批次不存在"}, status=404)
        running = list(batch.jobs.filter(status=JobStatus.RUNNING))
        for job in running:
            publish_cancel(job)
        if running:
            log_action(
                "quick_cancel", "command", f"批次 #{batch.pk}",
                detail=f"一键中断整批 {len(running)} 台仍在执行的机器：{batch.command[:120]}",
                result="info", actor=batch.actor,
            )
        return Response({
            "detail": f"已对 {len(running)} 台执行中的机器发送中断信号",
            "cancelled_job_ids": [j.pk for j in running],
        }, status=202)


class JobOutputView(APIView):
    def get(self, request, pk: int):
        job = Job.objects.filter(pk=pk, kind=JobKind.QUICK).first()
        if not job:
            return Response({"detail": "执行记录不存在"}, status=404)
        from .services import decode_output_entries

        archived = job.status != JobStatus.RUNNING
        raw = None
        try:
            raw = get_redis().lrange(job.stream_key, 0, -1)
            stream_text, stream_seq = decode_output_entries(raw)
        except Exception:  # noqa: BLE001
            stream_text, stream_seq = "", 0

        if archived:
            # 文本以数据库归档为准（Redis 7 天后会过期）；
            # 列表还在时用其中最大块序号压制水合前先到的 SSE 块，
            # 列表已空（过期）则一律视为历史，序号阈值拉满。
            output = job.output or stream_text
            max_seq = stream_seq if raw else 2**53 - 1
        else:
            output = stream_text or job.output
            max_seq = stream_seq

        return Response({
            "id": job.pk,
            "batch_id": job.batch_id,
            "host_id": job.host_id,
            "status": job.status,
            "exit_code": job.exit_code,
            "error_category": job.error_category,
            "error_message": job.error_message,
            "interrupted": job.interrupted,
            "output": output,
            "seq": max_seq,
            "archived": archived,
            "output_truncated": job.output_truncated,
        })


class HistoryView(APIView):
    """执行记录查询：支持 host_id、时间区间 [start, end)、状态过滤。"""

    def get(self, request):
        qs = Job.objects.filter(kind=JobKind.QUICK).select_related("batch", "host")

        host_id = request.query_params.get("host_id")
        if host_id:
            try:
                qs = qs.filter(host_id=int(host_id))
            except ValueError:
                return Response({"detail": "host_id 非法"}, status=400)

        for param, field in (("start", "created_at__gte"), ("end", "created_at__lt")):
            raw = request.query_params.get(param)
            if raw:
                parsed = parse_datetime(raw)
                if parsed is None:
                    return Response(
                        {"detail": f"{param} 需为 ISO 8601 时间，如 2026-09-20T10:00:00"},
                        status=400,
                    )
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed)
                qs = qs.filter(**{field: parsed})

        status_filter = request.query_params.get("status")
        if status_filter:
            valid = {s.value for s in JobStatus}
            if status_filter not in valid:
                return Response({"detail": f"status 需为 {sorted(valid)}"}, status=400)
            qs = qs.filter(status=status_filter)

        try:
            limit = min(int(request.query_params.get("limit", _DEFAULT_LIMIT)), _MAX_LIMIT)
        except ValueError:
            limit = _DEFAULT_LIMIT
        jobs = list(qs.order_by("-created_at")[:limit])
        return Response({
            "count": len(jobs),
            "records": [
                {
                    **_serialize_job(job),
                    "actor": job.batch.actor if job.batch else "admin",
                    "dangerous": job.batch.dangerous if job.batch else False,
                    "confirm_reason": job.batch.confirm_reason if job.batch else "",
                    "batch_created_at": (
                        job.batch.created_at.isoformat()
                        if job.batch and job.batch.created_at else None
                    ),
                }
                for job in jobs
            ],
        })


class BatchDetailView(APIView):
    def get(self, request, pk: int):
        batch = CommandBatch.objects.filter(pk=pk).first()
        if not batch:
            return Response({"detail": "批次不存在"}, status=404)
        jobs = batch.jobs.order_by("id")
        return Response({
            "id": batch.pk,
            "actor": batch.actor,
            "command": batch.command,
            "host_count": batch.host_count,
            "dangerous": batch.dangerous,
            "confirm_reason": batch.confirm_reason,
            "confirmed_at": batch.confirmed_at.isoformat() if batch.confirmed_at else None,
            "created_at": batch.created_at.isoformat(),
            "jobs": [_serialize_job(j) for j in jobs],
        })


def run_legacy_job_on_worker(job_id: int, host_id: int, command: str) -> dict:
    """旧的 /api/jobs/run：在常驻后台循环上执行并同步等结果（保持 201 语义）。"""
    from hosts.runner import run_ssh_job

    future = concurrent.futures.Future()
    import asyncio

    async def _wrap():
        try:
            future.set_result(await run_ssh_job(job_id, host_id, command))
        except Exception as exc:  # noqa: BLE001
            future.set_exception(exc)

    asyncio.run_coroutine_threadsafe(_wrap(), get_loop())
    return future.result(timeout=600)
