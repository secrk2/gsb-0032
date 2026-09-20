"""操作记录写入 + 作业输出缓冲（Redis）。"""
from __future__ import annotations

import asyncio
import json

import redis

from django.conf import settings

from .models import AuditLog, AuditResult

_redis = None
_async_redis = None

# Redis 是 best-effort 依赖：任何调用都不允许拖死执行器，
# 统一在这一层给硬超时并在连续失败时丢弃连接池重建。
_ASYNC_TIMEOUT = 3.0
# Redis 不可用时的进程内序号兜底（仅保活，不保证跨重启去重）
_fallback_seq: dict[str, int] = {}


def get_redis():
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(
            settings.REDIS_URL, decode_responses=True,
            socket_timeout=2, socket_connect_timeout=2,
        )
    return _redis


def get_async_redis():
    """进程级复用的异步 Redis 客户端（在后台 event loop 线程中使用）。

    必须显式给连接/读写超时：Redis 不可达且地址解析到被防火墙过滤的
    IPv6 时，默认无超时的连接会永久挂起，作业将永远停在「执行中」。
    """
    global _async_redis
    if _async_redis is None:
        import redis.asyncio as aioredis

        _async_redis = aioredis.from_url(
            settings.REDIS_URL, decode_responses=True,
            socket_timeout=2, socket_connect_timeout=2,
        )
    return _async_redis


async def _redis_async_call(method_name: str, *args, **kwargs):
    """统一的异步 Redis 调用：硬超时 + 失败时丢弃坏连接池。

    生产中 Redis 宕机/网络抖动时，redis-py 异步连接池里可能残留
    未完成握手的坏连接；socket_timeout 对「等待连接槽」不一定生效，
    因此这里用 asyncio.wait_for 兜底，并在异常后重建客户端。
    """
    global _async_redis
    client = get_async_redis()
    fn = getattr(client, method_name)
    try:
        return await asyncio.wait_for(fn(*args, **kwargs), timeout=_ASYNC_TIMEOUT)
    except Exception:  # noqa: BLE001 - best-effort
        try:
            await asyncio.wait_for(client.aclose(), timeout=1)
        except Exception:  # noqa: BLE001
            pass
        _async_redis = None
        return None


async def append_job_output_async(job, chunk: str) -> int:
    """异步写入一个输出块，返回该块的全局单调序号。

    序号与块一起以 JSON 存在列表里：前端先拉全量（含块数）做基线，
    再只追加 seq 更大的 SSE 块，断线/worker 重启都不会重放或漏块。
    """
    seq_key = f"zhiyue:job:{job.pk}:seq"
    seq = await _redis_async_call("incr", seq_key)
    if not isinstance(seq, int):
        # Redis 不可用：进程内自增兜底（块仍会归档进数据库，只是不做去重）
        seq = _fallback_seq[seq_key] = _fallback_seq.get(seq_key, 0) + 1
    await _redis_async_call(
        "rpush", job.stream_key, json.dumps({"s": seq, "d": chunk})
    )
    await _redis_async_call("expire", job.stream_key, 7 * 24 * 3600)
    await _redis_async_call("expire", seq_key, 7 * 24 * 3600)
    return seq


def decode_output_entries(raw: list[str]) -> tuple[str, int]:
    """把 Redis 里的块列表还原成（完整输出, 最大序号）。兼容旧的纯文本块。"""
    parts: list[str] = []
    max_seq = 0
    for item in raw or []:
        if not item:
            continue
        try:
            entry = json.loads(item)
            parts.append(entry["d"])
            max_seq = max(max_seq, int(entry.get("s", 0)))
        except (ValueError, TypeError, KeyError):
            parts.append(item)  # 旧格式纯文本
    return "".join(parts), max_seq


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
    """把作业输出增量写入 Redis 列表缓冲。

    不做条数截断（要求「输出多了不能截断」），仅设置 7 天过期；
    执行结束时完整内容会归档进 ``Job.output``，Redis 只承担实时流缓冲。
    """
    try:
        client = get_redis()
        client.rpush(job.stream_key, chunk)
        client.expire(job.stream_key, 7 * 24 * 3600)
    except Exception:  # noqa: BLE001 - 缓冲失败不应影响作业本身
        pass


def publish_cancel(job) -> None:
    """标记作业取消（执行器在另一个 uvicorn worker 上轮询此键）。

    用键标志而不是 pub/sub：跨 worker 同样生效，且不挑执行器此刻是否
    正好在线订阅；标志 1 小时自动过期，作业启动时也会主动清掉残留标志。
    """
    try:
        get_redis().set(f"zhiyue:job:{job.pk}:cancel", "1", ex=3600)
    except Exception:  # noqa: BLE001
        pass


async def clear_cancel_flag_async(job) -> None:
    await _redis_async_call("delete", f"zhiyue:job:{job.pk}:cancel")


async def redis_get_async(key: str):
    return await _redis_async_call("get", key)


async def redis_lrange_async(key: str):
    return await _redis_async_call("lrange", key, 0, -1)
