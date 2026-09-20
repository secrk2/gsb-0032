"""通过 asyncssh 在 Linux 主机上执行命令（快捷命令 / 普通作业共用）。

实时性：输出按块写入 Redis 流缓冲并经 SSE 广播 ``quick.output`` /
``job.output``，前端逐块刷新。

需求对应实现：
- **不截断**：Redis 不再 ltrim，全部输出原样缓冲；结束后完整归档到
  ``ops.Job.output``，历史回看不依赖 Redis。
- **颜色/中文不乱码**：申请 xterm-256color 伪终端保留 ANSI 颜色码；
  字节流经 UTF-8 增量解码器（多字节字符跨块到达也不会被切成乱码）。
- **可中断**：执行器轮询 Redis 取消标志键（取消请求可能来自另一个
  uvicorn worker）；收到后先发 Ctrl-C，宽限期内不退出再 terminate/kill。
- **错误分清**：连接超时 / 认证失败 / 端口不可达 / 凭据未配置分别归类，
  异常类别与信息写入 Job.error_category/error_message。
"""
from __future__ import annotations

import asyncio
import codecs
import socket

from asgiref.sync import sync_to_async
from django.utils import timezone

from ops.models import ErrorCategory, Job, JobStatus
from ops.services import (
    append_job_output_async,
    clear_cancel_flag_async,
    decode_output_entries,
    redis_get_async,
    redis_lrange_async,
)
from zhiyue.events import publish

_READ_CHUNK = 4096
# 取消标志轮询间隔
_CANCEL_POLL = 0.25
# 发出 Ctrl-C 后等远端自行退出的时间，超时再 terminate/kill
_INTERRUPT_GRACE = 3.0


def _publish_output(job_id: int, host_id, batch_id, chunk: str, seq: int) -> None:
    payload = {
        "id": job_id, "host_id": host_id, "batch_id": batch_id,
        "chunk": chunk, "seq": seq,
    }
    # 新入口订阅 quick.output；同时发 job.output 兼容旧作业页面
    publish("quick.output", payload)
    publish("job.output", {"id": job_id, "chunk": chunk, "seq": seq})


def _publish_finished(job_id: int, host_id, batch_id, result: dict) -> None:
    data = {"id": job_id, "host_id": host_id, "batch_id": batch_id, **result}
    publish("quick.finished", data)
    publish("job.finished", {
        "id": job_id, "status": result["status"], "exit_code": result["exit_code"],
    })


class _Utf8Decoder:
    """增量 UTF-8 解码：一个汉字被 TCP 切成两个包也不会变乱码。"""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def feed(self, data: bytes) -> str:
        return self._decoder.decode(data)

    def finish(self) -> str:
        return self._decoder.decode(b"", final=True)


@sync_to_async
def _load_host(host_id: int):
    from hosts.models import Host

    return _db(lambda: Host.objects.get(pk=host_id))


@sync_to_async
def _load_job(job_id: int):
    return _db(lambda: Job.objects.get(pk=job_id))


def _db(write_fn):
    """在后台线程里做一次同步 ORM 写，随即关闭连接。"""
    from django.db import connection

    try:
        return write_fn()
    finally:
        connection.close()


@sync_to_async
def _finish_job(job_id: int, **fields) -> None:
    def _write():
        Job.objects.filter(pk=job_id).update(finished_at=timezone.now(), **fields)

    return _db(_write)


async def _emit_chunk(job, host_id, batch_id, text: str) -> None:
    """写一块输出到 Redis（返回全局单调序号）并广播。"""
    if not text:
        return
    seq = await append_job_output_async(job, text)
    _publish_output(job.pk, host_id, batch_id, text, seq)


def classify_connect_error(exc: Exception) -> tuple[str, str]:
    """把连接阶段异常翻译成（类别, 中文说明）。

    注意 Python 3.11 起 ``asyncio.TimeoutError`` 是内建 ``TimeoutError``
    （OSError 子类），必须先于 OSError 判断。
    """
    import asyncssh

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return (
            ErrorCategory.TIMEOUT,
            f"连接超时：{exc}（规定时间内对端无响应，可能是网络不通或防火墙丢包）",
        )
    if isinstance(exc, (asyncssh.PermissionDenied, asyncssh.PasswordChangeRequired)):
        return ErrorCategory.AUTH, f"认证失败：账号、口令或私钥被对端拒绝（{exc}）"
    if isinstance(exc, asyncssh.HostKeyNotVerifiable):
        return ErrorCategory.AUTH, f"主机密钥校验失败：{exc}"
    if isinstance(exc, asyncssh.DisconnectError):
        code = getattr(exc, "code", None)
        if code in (13, 14, 15):  # NO_MORE_AUTH_METHODS / AUTH_CANCELLED 等
            return ErrorCategory.AUTH, f"认证被对端断开（code={code}）：{exc}"
        return ErrorCategory.CONNECTION, f"连接被对端断开（code={code}）：{exc}"
    if isinstance(exc, asyncssh.ConnectionLost):
        return ErrorCategory.CONNECTION, f"连接在执行过程中断开：{exc}"
    if isinstance(exc, ConnectionRefusedError):
        return ErrorCategory.CONNECTION, "连接被拒绝：目标端口没有 SSH 服务在监听"
    if isinstance(exc, socket.gaierror):
        return ErrorCategory.CONNECTION, f"地址无法解析：主机名/DNS 配置有误（{exc}）"
    if isinstance(exc, OSError):
        return ErrorCategory.CONNECTION, f"网络不可达或连接失败：{exc}"
    return ErrorCategory.UNKNOWN, f"未预期的异常：{type(exc).__name__}: {exc}"


async def _pump_stdout(proc, queue: asyncio.Queue) -> None:
    """持续读远端字节，读到 EOF 放一个 None 哨兵。"""
    try:
        while True:
            data = await proc.stdout.read(_READ_CHUNK)
            if not data:
                break
            await queue.put(data)
    except Exception as exc:  # noqa: BLE001
        await queue.put(exc)
    finally:
        await queue.put(None)


async def _run_process(job, proc, host_id, batch_id) -> bool:
    """驱动一个已建立的远端进程：转发输出、轮询取消。返回是否被中断。"""
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    pump = asyncio.ensure_future(_pump_stdout(proc, queue))
    decoder = _Utf8Decoder()
    cancel_key = f"zhiyue:job:{job.pk}:cancel"
    interrupted = False
    eof = False

    async def emit(text: str) -> None:
        await _emit_chunk(job, host_id, batch_id, text)

    try:
        while not eof:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_CANCEL_POLL)
            except asyncio.TimeoutError:
                cancelled = await redis_get_async(cancel_key) == "1"
                if cancelled:
                    interrupted = True
                    break
                continue

            if item is None:
                eof = True
                await emit(decoder.finish())
                break
            if isinstance(item, Exception):
                raise item
            await emit(decoder.feed(item))

        if interrupted:
            try:
                proc.stdin.write(b"\x03")  # Ctrl-C（encoding=None，写字节）
                await proc.stdin.drain()
            except Exception:  # noqa: BLE001 - 通道可能已关闭
                pass
            # 宽限期内继续抽取残余输出（远端通常收到 Ctrl-C 很快就会退出）
            deadline = asyncio.get_event_loop().time() + _INTERRUPT_GRACE
            while asyncio.get_event_loop().time() < deadline:
                try:
                    item = await asyncio.wait_for(
                        queue.get(),
                        timeout=max(0.05, deadline - asyncio.get_event_loop().time()),
                    )
                except asyncio.TimeoutError:
                    break
                if item is None:
                    await emit(decoder.finish())
                    break
                if isinstance(item, Exception):
                    break
                await emit(decoder.feed(item))
    finally:
        pump.cancel()

    return interrupted


async def execute_on_host(job_id: int, host_id: int, command: str) -> dict:
    """在一台主机上执行命令的完整生命周期，返回结果字典。"""
    import asyncssh

    job = await _load_job(job_id)
    host = await _load_host(host_id)
    await clear_cancel_flag_async(job)

    def fail(category: str, message: str, *, exit_code=None, interrupted=False):
        result = {
            "status": JobStatus.FAILED, "exit_code": exit_code,
            "error_category": category, "error_message": message,
            "interrupted": interrupted,
        }
        return result

    username = host.username
    password = host.get_password()
    private_key = host.get_private_key()
    if not username or (not password and not private_key):
        message = f"主机「{host.name}」未配置登录账号或凭据（口令/私钥），无法执行命令"
        notice = f"[执钥] {message}\n"
        await _emit_chunk(job, host_id, job.batch_id, notice)
        await _finish_job(job_id, output=notice, status=JobStatus.FAILED, exit_code=None,
                          error_category=ErrorCategory.CONFIG, error_message=message)
        result = fail(ErrorCategory.CONFIG, message)
        _publish_finished(job_id, host_id, job.batch_id, result)
        return result

    connect_kwargs: dict = {
        "host": host.address,
        "port": host.port,
        "username": username,
        "known_hosts": None,  # 内网纳管，不做主机密钥背书
        "connect_timeout": 8,
        "login_timeout": 8,
    }
    if private_key:
        connect_kwargs["client_keys"] = [asyncssh.import_private_key(private_key)]
    else:
        connect_kwargs["password"] = password

    header = f"[执钥] 在 {host.name}（{host.address}:{host.port}）执行: {command}\n"
    await _emit_chunk(job, host_id, job.batch_id, header)

    try:
        async with asyncssh.connect(**connect_kwargs) as conn:
            async with conn.create_process(
                command,
                term_type="xterm-256color",  # 申请 PTY：保留 ls/grep 的 ANSI 颜色
                term_size=(24, 200),
                encoding=None,              # 自己拿字节做增量 UTF-8 解码
            ) as proc:
                interrupted = await _run_process(job, proc, host_id, job.batch_id)
                if interrupted:
                    # Ctrl-C 后再给 2s 等远端退出状态；仍不退出才 terminate/kill
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2)
                    except asyncio.TimeoutError:
                        try:
                            proc.terminate()
                            await asyncio.wait_for(proc.wait(), timeout=2)
                        except Exception:  # noqa: BLE001
                            try:
                                proc.kill()
                            except Exception:  # noqa: BLE001
                                pass
                else:
                    # stdout EOF 后退出状态包可能还在路上，wait 确保拿到 exit_status
                    await asyncio.wait_for(proc.wait(), timeout=10)
                exit_code = proc.exit_status

        # 结束态：统一从 Redis 读全量缓冲（含 header），追加尾部说明后归档
        if interrupted:
            footer = f"\n[执钥] 已被用户中断，远端退出码: {exit_code}\n"
            result = fail(
                ErrorCategory.INTERRUPTED, "用户手动中断执行",
                exit_code=exit_code, interrupted=True,
            )
        else:
            footer = f"\n[执钥] 命令结束，退出码: {exit_code}\n"
            status = JobStatus.SUCCESS if exit_code == 0 else JobStatus.FAILED
            result = {
                "status": status, "exit_code": exit_code,
                "error_category": "",
                "error_message": "",
                "interrupted": False,
            }
            if exit_code != 0:
                result["error_category"] = ErrorCategory.REMOTE.value
                result["error_message"] = f"命令返回非零退出码 {exit_code}"

        await _emit_chunk(job, host_id, job.batch_id, footer)
        full_output = await _read_full_stream(job)
        await _finish_job(
            job_id,
            output=full_output,
            status=result["status"],
            exit_code=result["exit_code"],
            error_category=result["error_category"],
            error_message=result["error_message"],
            interrupted=result["interrupted"],
        )
        _publish_finished(job_id, host_id, job.batch_id, result)
        return result

    except Exception as exc:  # noqa: BLE001 - 统一归类落库，不让后台任务静默吞掉
        category, message = classify_connect_error(exc)
        footer = f"[执钥] {message}\n"
        result = fail(category, message)
        # footer 也写入 Redis 流；归档内容 = 已缓冲全部输出（含错误说明）
        await _emit_chunk(job, host_id, job.batch_id, footer)
        buffered = await _read_full_stream(job)
        await _finish_job(
            job_id, output=buffered,
            status=JobStatus.FAILED, exit_code=None,
            error_category=category, error_message=message,
        )
        _publish_finished(job_id, host_id, job.batch_id, result)
        return result


async def _read_full_stream(job) -> str:
    """读取 Redis 中本作业的全部缓冲输出（完整不截断）。"""
    raw = await redis_lrange_async(job.stream_key)
    text, _ = decode_output_entries(raw or [])
    return text


async def run_ssh_job(job_id: int, host_id: int, command: str) -> dict:
    """旧 ``POST /api/jobs/run`` 的入口，行为与快捷命令一致。"""
    return await execute_on_host(job_id, host_id, command)
