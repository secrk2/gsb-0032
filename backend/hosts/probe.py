"""主机在线探测。

- Linux/SSH：优先用主机上保存的账号做一次真实的 :mod:`asyncssh` 登录；
  没有配置凭据时退化为 SSH 端口 TCP 可达性检查。
- Windows/RDP：向 3389 发送标准的 X.224 Connection Request PDU，
  能读到对端响应即判定为在线（比单纯 TCP 连通更能说明 RDP 服务可用）。

探测结果回写 ``Host.status`` 并通过 SSE 广播，前端状态点实时变化。
"""
from __future__ import annotations

import asyncio
import socket
import time

from asgiref.sync import async_to_sync, sync_to_async
from django.utils import timezone

from zhiyue.events import publish

from .models import ConnectType, Host, HostStatus

# RDP X.224 Connection Request（TPKT 19B + COTP CR + RDP negotiation request，
# requestedProtocols = PROTOCOL_SSL|PROTOCOL_RDP = 0x00000003）
_RDP_CR_PDU = bytes.fromhex("030000130ee000000001000100080003000000")


async def _tcp_reachable(host: Host, timeout: float) -> bool:
    try:
        fut = asyncio.open_connection(host.address, host.port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return True
    except (OSError, asyncio.TimeoutError, socket.gaierror):
        return False


async def _probe_ssh(host: Host, timeout: float) -> bool:
    username = host.username
    password = host.get_password()
    private_key = host.get_private_key()
    if not username or (not password and not private_key):
        # 没有凭据：只验证端口
        return await _tcp_reachable(host, timeout)

    import asyncssh

    connect_kwargs: dict = {
        "host": host.address,
        "port": host.port,
        "username": username,
        "known_hosts": None,  # 内网纳管场景，不做主机密钥背书
        "connect_timeout": timeout,
        "login_timeout": timeout,
    }
    if private_key:
        connect_kwargs["client_keys"] = [asyncssh.import_private_key(private_key)]
    else:
        connect_kwargs["password"] = password
    try:
        async with asyncssh.connect(**connect_kwargs) as conn:
            result = await conn.run("true", timeout=timeout, check=False)
            return result.exit_status == 0
    except Exception:  # noqa: BLE001 - 任何连接/认证失败都按离线处理
        return False


async def _probe_rdp(host: Host, timeout: float) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host.address, host.port), timeout=timeout
        )
    except (OSError, asyncio.TimeoutError, socket.gaierror):
        return False
    try:
        writer.write(_RDP_CR_PDU)
        await writer.drain()
        data = await asyncio.wait_for(reader.read(19), timeout=timeout)
        # 对端回 TPKT（0x03 开头）即说明 RDP 服务有应答
        return bool(data) and data[0] == 0x03
    except (OSError, asyncio.TimeoutError):
        return False
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


async def probe_host_async(host_id: int) -> dict:
    host = await sync_to_async(Host.objects.get)(pk=host_id)
    from django.conf import settings

    timeout = settings.ONLINE_CHECK_TIMEOUT
    if host.connect_type == ConnectType.SSH:
        online = await _probe_ssh(host, timeout)
    else:
        online = await _probe_rdp(host, timeout)

    new_status = HostStatus.ONLINE if online else HostStatus.OFFLINE
    checked_at = timezone.now()

    await sync_to_async(_save_status)(host, new_status, checked_at)
    return {
        "id": host.pk,
        "name": host.name,
        "status": new_status,
        "status_checked_at": checked_at.isoformat(),
        "latency_ms": None,
    }


def _save_status(host: Host, new_status: str, checked_at) -> None:
    changed = host.status != new_status
    host.status = new_status
    host.status_checked_at = checked_at
    host.save(update_fields=["status", "status_checked_at", "updated_at"])
    if changed:
        publish("host.status", {
            "id": host.pk,
            "name": host.name,
            "status": new_status,
            "status_checked_at": checked_at.isoformat(),
        })


def probe_host(host: Host) -> dict:
    """同步包装，供 DRF 视图直接调用。"""
    started = time.monotonic()
    result = async_to_sync(probe_host_async)(host.pk)
    result["latency_ms"] = int((time.monotonic() - started) * 1000)
    return result
