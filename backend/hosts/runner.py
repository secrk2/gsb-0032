"""通过 asyncssh 在 Linux 主机上执行作业。

输出按块写入 Redis 列表缓冲，同时经 SSE 广播 ``job.output`` 事件，
前端可实时滚动查看。作业状态回写 :class:`ops.models.Job`。
"""
from __future__ import annotations

from asgiref.sync import sync_to_async
from django.utils import timezone

from ops.models import Job, JobStatus
from ops.services import append_job_output
from zhiyue.events import publish


@sync_to_async
def _load_host(host_id: int):
    from hosts.models import Host

    return Host.objects.get(pk=host_id)


@sync_to_async
def _finish_job(job_id: int, status: str, exit_code):
    Job.objects.filter(pk=job_id).update(
        status=status, exit_code=exit_code, finished_at=timezone.now()
    )
    publish("job.finished", {"id": job_id, "status": status, "exit_code": exit_code})


async def run_ssh_job(job_id: int, host_id: int, command: str) -> dict:
    import asyncssh

    host = await _load_host(host_id)
    connect_kwargs: dict = {
        "host": host.address,
        "port": host.port,
        "username": host.username,
        "known_hosts": None,
        "connect_timeout": 8,
        "login_timeout": 8,
    }
    private_key = host.get_private_key()
    if private_key:
        connect_kwargs["client_keys"] = [asyncssh.import_private_key(private_key)]
    else:
        connect_kwargs["password"] = host.get_password()

    job = await sync_to_async(Job.objects.get)(pk=job_id)
    try:
        async with asyncssh.connect(**connect_kwargs) as conn:
            async with conn.create_process(command) as proc:
                async for line in proc.stdout:
                    append_job_output(job, line)
                    publish("job.output", {"id": job_id, "chunk": line})
                stderr = await proc.stderr.read()
                if stderr:
                    append_job_output(job, stderr)
                    publish("job.output", {"id": job_id, "chunk": stderr})
                exit_code = proc.exit_status
        status = JobStatus.SUCCESS if exit_code == 0 else JobStatus.FAILED
        await _finish_job(job_id, status, exit_code)
        return {"status": status, "exit_code": exit_code}
    except Exception as exc:  # noqa: BLE001
        message = f"[执钥] 作业执行失败: {exc}\n"
        append_job_output(job, message)
        publish("job.output", {"id": job_id, "chunk": message})
        await _finish_job(job_id, JobStatus.FAILED, None)
        return {"status": JobStatus.FAILED, "exit_code": None, "error": str(exc)}
