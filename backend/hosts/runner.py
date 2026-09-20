"""通过 asyncssh 在 Linux 主机上执行快捷命令 / 作业。

设计要点：

- **PTY（xterm-256color）**：远端程序检测到真实终端后会输出 ANSI 颜色，
  字节流原样保存、原样经 SSE 转发，前端做转义渲染；编码固定 UTF-8（errors=replace），
  中文不会乱码。
- **输出不截断**：增量实时写 SSE，同时节流追加到 ``Job.output``（MySQL longtext），
  事后能完整回放，不再像 Redis 环形缓冲那样只留最后 1000 行。
- **可中断**：执行期间轮询 Redis 中断标记，命中后先 SIGINT 再 SIGKILL，
  最后关闭连接（PTY 主端关闭会给远端会话 SIGHUP 兜底）。
- **错误分类**：连接失败区分为超时 / 认证失败 / 拒绝 / 不可达 / 重置 / DNS /
  未配置凭据等，前端据此给出明确中文说明，而不是一句“连不上”。

该模块既被独立的 ``run_worker`` 进程异步消费队列调用，也被旧的同步
``POST /api/jobs/run`` 用 ``asyncio.run`` 直接调用。
"""
from __future__ import annotations

import asyncio
import errno
import re
import shlex
import socket

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db.models import Value
from django.db.models.functions import Concat
from django.utils import timezone

from ops.models import ErrorKind, Job, JobStatus
from zhiyue.events import publish

# 单帧 SSE 不宜过大，超长输出按字节切片后逐帧推；落库仍是完整整段
_SSE_CHUNK_BYTES = 8192

# 中断标记轮询间隔 / SIGINT 后宽限 / 强制杀
_ABORT_POLL_SECONDS = 0.3
_ABORT_GRACE_SECONDS = 2.0

# 让远端 shell 先通过“窗口标题”OSC 序列把自己的 tty 回传（对终端不可见，
# 输出到达时会被剥离）。中断时据此 pkill -t 杀掉整个终端会话。
_TTY_MARKER_RE = re.compile(r"\x1b\]0;__ZY_TTY:([^\x07]+)\x07")
_TTY_PREFIX = """printf '\\033]0;__ZY_TTY:%s\\007' "$(tty 2>/dev/null)"; """
_MARKER_BEGIN = "\x1b]0;__ZY_TTY:"


class _MarkerFilter:
    """从输出流中剥离 tty 标记 OSC 序列。

    标记可能被 TCP 分包切成两半，尾部遇到疑似未闭合的序列时先缓存，
    等下一段拼齐；流结束时调用 :meth:`flush` 释放残余（避免正常输出被吞）。
    """

    def __init__(self, on_tty):
        self._hold = ""
        self._on_tty = on_tty

    def feed(self, text: str) -> str:
        data = self._hold + text
        self._hold = ""

        def _capture(match: re.Match) -> str:
            self._on_tty(match.group(1).strip().removeprefix("/dev/"))
            return ""

        data = _TTY_MARKER_RE.sub(_capture, data)

        # 尾部若有一段未闭合的 ESC 开头序列，缓存到下一段
        esc = data.rfind("\x1b")
        if esc >= 0 and "\x07" not in data[esc:]:
            tail = data[esc:]
            if _MARKER_BEGIN.startswith(tail) or tail.startswith("\x1b]"):
                self._hold = tail
                data = data[:esc]
        return data

    def flush(self) -> str:
        rest, self._hold = self._hold, ""
        return rest


def connect_timeout() -> float:
    return float(getattr(settings, "JOB_CONNECT_TIMEOUT", 10))


# ---------------------------------------------------------------------------
# 同步 DB 小工具（都在 async 上下文的线程池里执行）
# ---------------------------------------------------------------------------
@sync_to_async
def _load_job(job_id: int) -> Job | None:
    return Job.objects.filter(pk=job_id).first()


@sync_to_async
def _load_host(host_id: int):
    from hosts.models import Host

    return Host.objects.filter(pk=host_id).first()


@sync_to_async
def _mark_started(job_id: int) -> None:
    Job.objects.filter(pk=job_id).update(started_at=timezone.now())


@sync_to_async
def _append_output(job_id: int, text: str) -> None:
    """用 SQL CONCAT 追加，避免把整段大输出读回 Python 再写。"""
    if text:
        Job.objects.filter(pk=job_id).update(
            output=Concat("output", Value(text))
        )


@sync_to_async
def _finish_job(job_id: int, *, status: str, exit_code, error_kind: str = "",
                error_message: str = "") -> None:
    Job.objects.filter(pk=job_id).update(
        status=status,
        exit_code=exit_code,
        error_kind=error_kind or "",
        error_message=error_message or "",
        finished_at=timezone.now(),
    )
    publish("job.finished", {
        "id": job_id,
        "status": status,
        "exit_code": exit_code,
        "error_kind": error_kind or "",
        "error_message": error_message or "",
    })


def _emit(job_id: int, chunk: str) -> None:
    """实时推一帧（或分帧）输出给前端。"""
    if not chunk:
        return
    rest = chunk
    while len(rest.encode("utf-8", errors="replace")) > _SSE_CHUNK_BYTES:
        # 按字符切到不超过限额（中文按整字符，避免切坏 UTF-8）
        cut = 0
        size = 0
        for ch in rest:
            n = len(ch.encode("utf-8", errors="replace"))
            if size + n > _SSE_CHUNK_BYTES:
                break
            size += n
            cut += 1
        publish("job.output", {"id": job_id, "chunk": rest[:cut]})
        rest = rest[cut:]
    if rest:
        publish("job.output", {"id": job_id, "chunk": rest})


# ---------------------------------------------------------------------------
# 错误分类
# ---------------------------------------------------------------------------
def classify_connect_error(exc: BaseException, *, host: str, port: int,
                           username: str) -> tuple[str, str]:
    """把底层各种异常归类为 ErrorKind + 给人看的中文说明。"""
    import asyncssh

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return (
            ErrorKind.TIMEOUT,
            f"连接 {host}:{port} 超时（{connect_timeout():.0f} 秒内没有响应）："
            "机器可能已关机、网络不通、安全组未放行或 SSH 服务未监听。",
        )

    if isinstance(exc, asyncssh.PermissionDenied):
        reason = getattr(exc, "reason", None)
        auth_result = getattr(exc, "auth_result", None)
        tried = ""
        try:
            allowed = list(auth_result.allowed_auths or []) if auth_result else []
            if allowed:
                tried = f"；对端仅接受：{', '.join(allowed)}"
        except Exception:  # noqa: BLE001
            pass
        detail = f"（对端返回：{reason}）" if reason else ""
        return (
            ErrorKind.AUTH_FAILED,
            f"账号「{username}」认证失败{detail}{tried}："
            "请核对用户名、口令或私钥是否正确，以及该账号是否被允许登录。",
        )

    _pwd_change = getattr(asyncssh, "PasswordChangeRequired", None)
    if _pwd_change is not None and isinstance(exc, _pwd_change):
        return (
            ErrorKind.AUTH_FAILED,
            f"账号「{username}」的口令已过期，对端要求先修改口令后才能登录，"
            "请在主机上完成改密（或更新平台保存的凭据）后再执行。",
        )

    if isinstance(exc, (asyncssh.HostKeyNotVerifiable,)):
        return (
            ErrorKind.SSH_ERROR,
            f"SSH 主机密钥校验失败：{exc}。平台默认不校验 known_hosts，"
            "出现此错误通常是对端 SSH 服务异常或被中间人代理。",
        )

    _proto_errors = tuple(
        cls for name in (
            "KeyExchangeFailed", "ProtocolError", "KeyImportError", "MACError",
            "CompressionError", "DisabledConnectionEncryption",
        )
        if (cls := getattr(asyncssh, name, None)) is not None
    )
    if isinstance(exc, _proto_errors):
        return (
            ErrorKind.SSH_ERROR,
            f"SSH 协议协商失败（{type(exc).__name__}）：{exc}。"
            "常见于对端 SSH 版本/算法过旧、配置不兼容，或 22 端口后并非 SSH 服务。",
        )

    if isinstance(exc, asyncssh.ChannelOpenError):
        return (
            ErrorKind.SSH_ERROR,
            f"SSH 通道被对端拒绝（{getattr(exc, 'reason', exc)}）："
            "可能是最大会话数已满、账号被限制或 SFTP/exec 子系统不可用。",
        )

    if isinstance(exc, ConnectionRefusedError):
        return (
            ErrorKind.CONNECTION_REFUSED,
            f"{host}:{port} 主动拒绝连接：端口可达但没有 SSH 服务在监听"
            "（sshd 未启动 / 端口配置错误 / 被防火墙 REJECT）。",
        )

    if isinstance(exc, ConnectionResetError):
        return (
            ErrorKind.CONNECTION_RESET,
            f"{host}:{port} 接受连接后立即重置：SSH 服务可能异常崩溃、"
            "被 TCP Wrapper / fail2ban / 安全软件拦截，或对方限制了来源 IP。",
        )

    if isinstance(exc, socket.gaierror):
        return (
            ErrorKind.DNS_ERROR,
            f"主机名「{host}」无法解析为 IP 地址：请检查地址拼写与 DNS 配置。",
        )

    if isinstance(exc, OSError):
        code = getattr(exc, "errno", None)
        host_down = getattr(errno, "EHOSTDOWN", 112)
        if code in (errno.EHOSTUNREACH, errno.ENETUNREACH, host_down,
                    socket.EAI_AGAIN):
            return (
                ErrorKind.HOST_UNREACHABLE,
                f"路由不可达，无法到达 {host}:{port}（{exc}）："
                "机器可能离线、跨机房网段不通或 VPN/专线中断。",
            )
        if code in (errno.ETIMEDOUT, errno.EAGAIN):
            return (
                ErrorKind.TIMEOUT,
                f"与 {host}:{port} 的连接超时（{exc}）：网络丢包或防火墙 DROP。",
            )
        if code == errno.ECONNREFUSED:
            return (
                ErrorKind.CONNECTION_REFUSED,
                f"{host}:{port} 拒绝连接（{exc}）：SSH 服务可能未启动或端口不对。",
            )
        if code == errno.ECONNRESET:
            return (
                ErrorKind.CONNECTION_RESET,
                f"{host}:{port} 重置了连接（{exc}），可能被安全策略拦截。",
            )
        return (
            ErrorKind.HOST_UNREACHABLE,
            f"无法连接 {host}:{port}（OSError {code}: {exc}）。",
        )

    if isinstance(exc, asyncssh.ConnectionLost):
        return (
            ErrorKind.CONNECTION_RESET,
            f"SSH 连接在执行过程中断开：{exc}。可能是网络抖动、sshd 被杀或机器重启。",
        )

    return ErrorKind.ERROR, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# 执行主流程
# ---------------------------------------------------------------------------
async def run_ssh_job(job_id: int, host_id: int, command: str) -> dict:
    """在一台 Linux 主机上执行命令；返回 {status, exit_code, error_kind, error_message}。"""
    import asyncssh

    await _mark_started(job_id)
    host = await _load_host(host_id)
    if host is None:
        msg = f"目标主机（id={host_id}）已不存在，无法执行。"
        _emit(job_id, f"\x1b[31;1m[执钥] {msg}\x1b[0m\r\n")
        await _append_output(job_id, f"[执钥] {msg}\n")
        await _finish_job(job_id, status=JobStatus.FAILED, exit_code=None,
                          error_kind=ErrorKind.ERROR, error_message=msg)
        return {"status": JobStatus.FAILED, "exit_code": None,
                "error_kind": ErrorKind.ERROR, "error_message": msg}

    password = host.get_password()
    private_key = host.get_private_key()
    if not host.username or (not password and not private_key):
        msg = (f"主机「{host.name}」未配置可用的登录凭据（账号/口令/私钥），"
               "请先在主机信息里补全后再执行。")
        _emit(job_id, f"\x1b[31;1m[执钥] {msg}\x1b[0m\r\n")
        await _append_output(job_id, f"[执钥] {msg}\n")
        await _finish_job(job_id, status=JobStatus.FAILED, exit_code=None,
                          error_kind=ErrorKind.NO_CREDENTIAL, error_message=msg)
        return {"status": JobStatus.FAILED, "exit_code": None,
                "error_kind": ErrorKind.NO_CREDENTIAL, "error_message": msg}

    connect_kwargs: dict = {
        "host": host.address,
        "port": host.port,
        "username": host.username,
        "known_hosts": None,  # 内网纳管，不做主机密钥背书
        "connect_timeout": connect_timeout(),
        "login_timeout": connect_timeout(),
        "encoding": "utf-8",
        "errors": "replace",  # 个别脏字节不致整体乱码
    }
    if private_key:
        connect_kwargs["client_keys"] = [asyncssh.import_private_key(private_key)]
    else:
        connect_kwargs["password"] = password

    try:
        conn = await asyncssh.connect(**connect_kwargs)
    except Exception as exc:  # noqa: BLE001 - 建连失败统一分类
        kind, message = classify_connect_error(
            exc, host=host.address, port=host.port, username=host.username
        )
        _emit(job_id, f"\x1b[31;1m[执钥] {message}\x1b[0m\r\n")
        await _append_output(job_id, f"[执钥] {message}\n")
        await _finish_job(job_id, status=JobStatus.FAILED, exit_code=None,
                          error_kind=kind, error_message=message)
        return {"status": JobStatus.FAILED, "exit_code": None,
                "error_kind": kind, "error_message": message}

    timeout = connect_timeout()
    aborted = asyncio.Event()
    pending: list[str] = []

    async def pump_loop() -> None:
        """每 100ms 把缓冲的输出合并后一次性推 SSE 并落库（大输出时不刷碎帧）。"""
        while True:
            await asyncio.sleep(0.1)
            if pending:
                text = "".join(pending)
                pending.clear()
                _emit(job_id, text)
                await _append_output(job_id, text)

    async def watch_abort(conn_state: dict) -> None:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2
        )
        key = f"zhiyue:job:{job_id}:abort"
        try:
            while True:
                try:
                    hit = await client.exists(key)
                except Exception:  # noqa: BLE001 - Redis 抖动不影响执行
                    hit = False
                if hit:
                    aborted.set()
                    # 只入缓冲，由 pump_loop 统一推给前端
                    pending.append(
                        "\r\n\x1b[33;1m[执钥] 收到中断请求，正在停止远端进程…\x1b[0m\r\n"
                    )
                    asyncio.create_task(_terminate(conn_state))
                    return
                await asyncio.sleep(_ABORT_POLL_SECONDS)
        finally:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    proc = None
    pump = None
    watcher = None
    status = JobStatus.FAILED
    exit_code = None
    error_kind = ""
    error_message = ""
    # 跨协程共享：远端 tty / 进程 / 连接，供中断协程使用
    conn_state: dict = {"proc": None, "conn": conn, "tty": None}
    try:
        async with conn:
            proc = await conn.create_process(
                _TTY_PREFIX + command,
                term_type="xterm-256color",  # 请求 PTY：保留颜色、合并 stderr
                term_size=(120, 32),
                encoding="utf-8",
                errors="replace",
            )
            conn_state["proc"] = proc
            pump = asyncio.create_task(pump_loop())
            watcher = asyncio.create_task(watch_abort(conn_state))

            marker_filter = _MarkerFilter(
                lambda tty: conn_state.__setitem__("tty", tty)
            )
            async for raw in proc.stdout:
                chunk = marker_filter.feed(raw)
                if chunk:
                    pending.append(chunk)

            tail_mark = marker_filter.flush()
            if tail_mark:
                pending.append(tail_mark)

            # EOF：等退出状态落定（中断路径通常已结束）
            await asyncio.wait_for(proc.wait(), timeout=timeout)
            exit_status = proc.exit_status
            exit_signal = getattr(proc, "exit_signal", None)

            if aborted.is_set():
                status = JobStatus.ABORTED
                exit_code = None
                error_kind = ErrorKind.ABORTED
                error_message = "命令被用户在平台上手动中断。"
                tail = "\r\n\x1b[33;1m[执钥] 命令已中断\x1b[0m\r\n"
            elif exit_signal:
                # 被信号杀死（非用户主动中断）
                status = JobStatus.FAILED
                exit_code = None
                error_kind = ErrorKind.ERROR
                error_message = f"进程被信号 {exit_signal} 终止。"
                tail = f"\r\n\x1b[31;1m[执钥] 进程被信号 {exit_signal} 终止\x1b[0m\r\n"
            elif exit_status == 0:
                status = JobStatus.SUCCESS
                exit_code = 0
                tail = f"\r\n\x1b[90m[执钥] 执行完成，退出码 0\x1b[0m\r\n"
            else:
                status = JobStatus.FAILED
                exit_code = exit_status
                error_message = f"命令退出码非 0（exit={exit_status}），请查看上方错误输出。"
                tail = (f"\r\n\x1b[31;1m[执钥] 命令执行失败，"
                        f"退出码 {exit_status}\x1b[0m\r\n")
            pending.append(tail)
    except asyncio.TimeoutError:
        if aborted.is_set():
            status = JobStatus.ABORTED
            error_kind = ErrorKind.ABORTED
            error_message = "命令被用户在平台上手动中断。"
        else:
            status = JobStatus.FAILED
            error_kind = ErrorKind.TIMEOUT
            error_message = f"等待远端进程结束超过 {timeout:.0f} 秒。"
            pending.append(f"\r\n\x1b[31;1m[执钥] {error_message}\x1b[0m\r\n")
    except Exception as exc:  # noqa: BLE001 - 执行期断链等
        if aborted.is_set():
            status = JobStatus.ABORTED
            error_kind = ErrorKind.ABORTED
            error_message = "命令被用户在平台上手动中断。"
        else:
            error_kind, error_message = classify_connect_error(
                exc, host=host.address, port=host.port, username=host.username
            )
            pending.append(f"\r\n\x1b[31;1m[执钥] {error_message}\x1b[0m\r\n")
    finally:
        if watcher:
            watcher.cancel()
        if pump:
            pump.cancel()
        if pending:
            text = "".join(pending)
            pending.clear()
            _emit(job_id, text)
            await _append_output(job_id, text)
        # 连接兜底清理
        try:
            conn.abort()
        except Exception:  # noqa: BLE001
            pass
        await _finish_job(
            job_id, status=status, exit_code=exit_code,
            error_kind=error_kind, error_message=error_message,
        )

    return {"status": status, "exit_code": exit_code,
            "error_kind": error_kind, "error_message": error_message}


async def _terminate(conn_state: dict) -> None:
    """中断远端命令。

    OpenSSH 通道普遍不支持 ``signal`` 消息，因此：
    1. 向 PTY 写入 ``\\x03``（Ctrl-C）——前台进程组收到 SIGINT，能自行清理；
    2. 宽限后若仍未退出，用回传的 tty 名 ``pkill -KILL -t`` 杀掉整个终端会话；
    3. 再不行关闭 SSH 连接（PTY 主端关闭产生 SIGHUP 兜底）。
    """
    proc = conn_state.get("proc")
    conn = conn_state.get("conn")
    try:
        try:
            proc.stdin.write("\x03")
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(_ABORT_GRACE_SECONDS)

        if proc.returncode is None and conn_state.get("tty"):
            tty = conn_state["tty"]
            try:
                await conn.run(
                    f"pkill -KILL -t {shlex.quote(tty)} 2>/dev/null",
                    check=False, timeout=5,
                )
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.8)

        if proc.returncode is None:
            conn.abort()
    except Exception:  # noqa: BLE001
        try:
            conn.abort()
        except Exception:  # noqa: BLE001
            pass
