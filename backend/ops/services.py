"""操作记录写入、作业队列与输出缓冲（Redis）。"""
from __future__ import annotations

import redis

from django.conf import settings

from .models import AuditLog, AuditResult

_redis = None

# 快捷命令作业队列：视图层 RPUSH 入队，run_worker 进程 BLPOP 取出后经 asyncssh 执行
JOB_QUEUE_KEY = "zhiyue:jobs:queue"


def get_redis():
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


def log_action(action: str, target_type: str, target_name: str = "",
               detail: str = "", result: str = AuditResult.SUCCESS,
               actor: str = "admin") -> AuditLog:
    """写一条操作记录，并通过 SSE 实时推给前端。"""
    from zhiyue.events import publish  # 延迟导入避免 app 加载顺序问题

    entry = AuditLog.objects.create(
        actor=actor, action=action, target_type=target_type,
        target_name=target_name, detail=detail, result=result,
    )
    publish("audit", {
        "id": entry.pk,
        "actor": entry.actor,
        "action": entry.action,
        "target_type": entry.target_type,
        "target_name": entry.target_name,
        "detail": entry.detail,
        "result": entry.result,
        "created_at": entry.created_at.isoformat(),
    })
    return entry


def enqueue_jobs(job_ids: list[int]) -> None:
    """把新建的作业 id 放入执行队列（run_worker 进程消费）。"""
    if not job_ids:
        return
    client = get_redis()
    client.rpush(JOB_QUEUE_KEY, *[str(j) for j in job_ids])


def abort_key(job_id: int) -> str:
    return f"zhiyue:job:{job_id}:abort"


def request_abort(job_id: int, ttl_seconds: int = 3600) -> None:
    """置中断标记：执行器轮询到后先 SIGINT 再 SIGKILL 远端进程。"""
    get_redis().set(abort_key(job_id), "1", ex=ttl_seconds)


def clear_abort(job_id: int) -> None:
    get_redis().delete(abort_key(job_id))


def queue_depth() -> int:
    try:
        return get_redis().llen(JOB_QUEUE_KEY)
    except Exception:  # noqa: BLE001
        return 0


def append_job_output(job, chunk: str) -> None:
    """旧接口兼容：把输出增量写入 Redis 列表缓冲。

    新链路输出完整落库（``Job.output``），此函数仅为历史调用保留。
    """
    try:
        client = get_redis()
        client.rpush(job.output_key, chunk)
        client.expire(job.output_key, 7 * 24 * 3600)
    except Exception:  # noqa: BLE001 - 缓冲失败不应影响作业本身
        pass
