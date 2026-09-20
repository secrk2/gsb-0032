"""危险命令识别。

快捷命令入口允许一次向多台机器下发命令，误操作的爆炸半径很大，因此在真正
执行前做一次静态规则匹配；命中规则只给出「需要二次确认」的中文原因，
用户确认并填写确认原因后仍可执行（确认原因随批次落库，见 CommandBatch）。

只做启发式识别（不是完整 shell 语法解析），宁可多提示，不可漏提示：
- 按 ``;`` ``&&`` ``||`` ``|`` ``&`` 换行拆成子命令逐条检查；
- 兼容 ``sudo`` / ``nohup`` / 环境变量赋值前缀和合并短选项（``-rf``）。
"""
from __future__ import annotations

import re

# :(){ :|:& };: 及其常见变体
_FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:?\s*&[^}]*\}")

# dd 写入整块设备
_DD_TO_DEVICE = re.compile(r"(^|\s)of=/dev/(sd[a-z]+|nvme\d+n\d+|vd[a-z]+|xvd[a-z]+|disk|diskette)\b")

# 直接重定向覆盖块设备，如 mkfs.xfs /dev/sda1、cat x > /dev/sda
_BLOCK_DEV = re.compile(r"/dev/(sd[a-z0-9]+|nvme\d+n\d+p?\d*|vd[a-z0-9]+|xvd[a-z0-9]+)")

_SEP_RE = re.compile(r"\n|&&|\|\||;|\||&")
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_LEADING_UTILS = {"sudo", "nohup", "command", "exec", "time", "env"}


def _split_subcommands(command: str) -> list[str]:
    """粗拆 shell 命令行。注释/引号内的分隔符也会拆开——对安全检查宁多勿漏。"""
    parts = _SEP_RE.split(command)
    return [p.strip() for p in parts if p.strip()]


def _tokens(sub: str) -> list[str]:
    try:
        import shlex

        toks = shlex.split(sub, comments=False, posix=True)
    except ValueError:
        # 引号没闭合时退回空白拆分，保证仍能命中裸写的危险命令
        toks = sub.split()
    # 剥掉前缀：sudo、环境变量赋值等
    while toks and (_ASSIGN_RE.match(toks[0]) or toks[0] in _LEADING_UTILS):
        if _ASSIGN_RE.match(toks[0]):
            toks = toks[1:]
        else:
            # sudo 后可能跟 -u user
            toks = toks[1:]
            if toks and toks[0] in ("-u", "--user"):
                toks = toks[2:]
    return toks


def _has_flag(toks: list[str], *names: str) -> bool:
    """是否出现指定选项。合并短选项（-rf）按字母逐个匹配，长选项支持 = 值。"""
    short = {n.lstrip("-") for n in names
             if not n.startswith("--") and len(n.lstrip("-")) == 1}
    long = {n for n in names if n.startswith("--")}
    exact = set(names)
    for t in toks[1:]:
        if t in exact:
            return True
        if t.startswith("--"):
            if t.split("=", 1)[0] in long:
                return True
        elif t.startswith("-") and len(t) > 1:
            letters = {ch for ch in t[1:] if ch.isalpha()}
            if letters & short:
                return True
    return False


def _path_args(toks: list[str]) -> list[str]:
    return [t for t in toks[1:] if not t.startswith("-")]


# 裸写即致命的系统目录（/、/etc、~ …）
_BARE_SYSTEM = {
    "/", "/root", "/etc", "/boot", "/usr", "/bin", "/sbin", "/lib", "/lib64",
    "/var", "/sys", "/proc", "/dev", "~", "$HOME",
}


def _check_rm(toks: list[str]) -> str | None:
    recursive = _has_flag(toks, "-r", "-R", "--recursive")
    force = _has_flag(toks, "-f", "--force")
    no_preserve = "--no-preserve-root" in toks
    paths = _path_args(toks)
    if no_preserve:
        return "rm 带 --no-preserve-root，明确允许删除根目录，会清空整机文件"
    if not (recursive or force) or not paths:
        return None
    for p in paths:
        bare = p.rstrip("/") or "/"
        if p in ("*", "/*"):
            return "递归强制删除根目录通配「*」，将清空整机文件"
        if bare in _BARE_SYSTEM:
            return f"递归强制删除系统关键目录「{p}」，会导致机器不可用甚至无法启动"
    return None


def _check_chmod_chown(toks: list[str], util: str) -> str | None:
    if not _has_flag(toks, "-R", "--recursive"):
        return None
    for p in _path_args(toks):
        bare = p.rstrip("/") or "/"
        if bare in _BARE_SYSTEM or p.rstrip("/") in _BARE_SYSTEM:
            return f"对系统关键目录「{p}」递归{util}，会批量破坏系统权限"
    return None


def _check_subcommand(sub: str) -> str | None:
    toks = _tokens(sub)
    if not toks:
        return None
    util = toks[0].rsplit("/", 1)[-1]

    if util == "rm":
        return _check_rm(toks)

    if util in ("mkfs", "mkfs.ext2", "mkfs.ext3", "mkfs.ext4", "mkfs.xfs",
                "mkfs.btrfs", "mkfs.vfat", "mkfs.ntfs", "mkswap"):
        for t in toks[1:]:
            if _BLOCK_DEV.search(t) or t.startswith("/dev/"):
                return f"{util} 对块设备「{t}」建文件系统，盘上数据将被清空"
        return f"{util} 会格式化文件系统，盘上数据不可恢复"

    if util == "dd":
        joined = " ".join(toks[1:])
        m = _DD_TO_DEVICE.search(joined)
        if m:
            return "dd 直接写入块设备，会不可逆地覆盖磁盘数据"

    if util in ("shutdown", "halt", "poweroff", "reboot", "init", "telinit"):
        args = toks[1:]
        if util in ("init", "telinit"):
            if any(a in ("0", "6") for a in args):
                return "切换到系统运行级 0/6，主机会立即关机或重启"
            return None
        return f"{util} 会立即关闭/重启主机，其上业务会中断"

    if util == "iptables" or util.endswith("/iptables"):
        if _has_flag(toks, "-F", "--flush"):
            return "清空防火墙规则（iptables -F）可能使主机暴露或被断网"

    if util in ("chmod",):
        return _check_chmod_chown(toks, "改权限")
    if util in ("chown",):
        return _check_chmod_chown(toks, "改属主")

    if util in ("kill", "killall", "pkill"):
        joined = " ".join(toks)
        # kill -9 -1 / kill -TERM -- ：向除 init 外全部进程发信号
        if util == "kill" and re.search(r"(?:^|\s)-[A-Za-z0-9]+\s+(?:-\s*1|--)\b", joined):
            return "向除 init 外的全部进程发信号（kill ... -1），会打挂整机所有服务"
        if util in ("killall", "pkill") and re.search(r"(?:^|\s)sshd(?:\s|$)", joined):
            return "杀掉 sshd 会导致远程连接立刻断开且可能无法再登录"

    # 重定向覆盖块设备：> /dev/sda
    if _BLOCK_DEV.search(sub) and ">" in sub:
        m = _BLOCK_DEV.search(sub)
        return f"重定向写入块设备「{m.group(0)}」，会破坏磁盘分区/文件系统"

    return None


def detect_dangerous(command: str) -> list[str]:
    """返回命中的危险原因列表（去重、保序）；空列表表示安全。"""
    if not command:
        return []
    reasons: list[str] = []
    seen: set[str] = set()

    def add(reason: str | None) -> None:
        if reason and reason not in seen:
            seen.add(reason)
            reasons.append(reason)

    if _FORK_BOMB.search(command):
        add("fork 炸弹（快速耗尽进程/内存资源，主机会卡死）")

    for sub in _split_subcommands(command):
        add(_check_subcommand(sub))
    return reasons


def is_dangerous(command: str) -> bool:
    return bool(detect_dangerous(command))
