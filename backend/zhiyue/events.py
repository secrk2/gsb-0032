"""实时事件总线：写操作发布到 Redis，SSE 视图订阅后推给浏览器。

- :func:`publish` 供同步代码（DRF 视图 / 探测线程）调用；
- :func:`event_stream` 是异步生成器，订阅 Redis pub/sub，
  Redis 暂时不可用时不断开浏览器，而是发心跳并自动重连。

没有 Redis（如本地 sqlite 冒烟）时 publish 静默降级，不影响主流程。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from django.conf import settings

_sync_redis = None


def _get_sync_redis():
    global _sync_redis
    if _sync_redis is None:
        import redis  # 延迟导入，便于无 redis 环境跑 check

        _sync_redis = redis.Redis.from_url(
            settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2
        )
    return _sync_redis


def publish(event_type: str, data: dict[str, Any]) -> None:
    """发布一条事件（best-effort，失败不影响业务事务）。"""
    payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False,
                         default=str)
    try:
        _get_sync_redis().publish(settings.REDIS_EVENTS_CHANNEL, payload)
    except Exception:  # noqa: BLE001 - 实时推送不能拖垮写接口
        pass


def _frame(event_type: str, data: Any) -> str:
    return (f"event: {event_type}\n"
            f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n")


async def event_stream(last_event_id: str | None = None):
    """SSE 异步生成器：产出 ``event:`` / ``data:`` 文本帧。

    建链先发 hello；Redis 断线期间发 SSE 注释心跳并重连，保证代理不掐连接。
    """
    import redis.asyncio as aioredis

    yield _frame("hello", {"message": "connected"})

    while True:
        client = aioredis.from_url(
            settings.REDIS_URL, socket_timeout=15, socket_connect_timeout=5
        )
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(settings.REDIS_EVENTS_CHANNEL)
        except Exception:  # noqa: BLE001 - Redis 未就绪：心跳 + 退避重连
            await pubsub.aclose()
            await client.aclose()
            yield ": waiting for redis\n\n"
            await asyncio.sleep(3)
            continue

        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=15.0
                )
                if message is not None:
                    try:
                        parsed = json.loads(message["data"])
                        yield _frame(parsed.get("type", "message"),
                                     parsed.get("data", {}))
                    except (ValueError, TypeError, KeyError):
                        continue
                else:
                    # 心跳，防止代理断连
                    yield ": ping\n\n"
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 连接中断：回到重连循环
            yield ": reconnecting\n\n"
            await asyncio.sleep(1)
        finally:
            try:
                await pubsub.unsubscribe(settings.REDIS_EVENTS_CHANNEL)
            except Exception:  # noqa: BLE001
                pass
            await pubsub.aclose()
            await client.aclose()
