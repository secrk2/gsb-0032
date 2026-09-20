"""操作记录写入 + 作业输出缓冲（Redis）。"""
from __future__ import annotations

import redis

from django.conf import settings

from .models import AuditLog, AuditResult

_redis = None


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


def append_job_output(job, chunk: str) -> None:
    """把作业输出增量写入 Redis 列表缓冲（保留最近 1000 行）。"""
    try:
        client = get_redis()
        client.rpush(job.output_key, chunk)
        client.ltrim(job.output_key, -1000, -1)
        client.expire(job.output_key, 7 * 24 * 3600)
    except Exception:  # noqa: BLE001 - 缓冲失败不应影响作业本身
        pass
