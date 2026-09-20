"""危险命令识别。

命中危险命令的快捷执行必须先经用户确认，且确认原因会随批次/作业落库、
写操作记录，形成留痕。检测在**服务端**做（前端提示只是体验，不能作为安全边界）。

策略：正则规则 + 归一化后的 shell 文本匹配（去注释 / 折叠空白），
并对 ``command``、``command && command``、管道两侧分别检测，避免被
``echo x && rm -rf /`` 之类的复合命令绕过。
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class DangerRule:
    key: str
    label: str
    pattern: re.Pattern
    advice: str


def _rule(key: str, label: str, regex: str, advice: str) -> DangerRule:
    return DangerRule(key=key, label=label, pattern=re.compile(regex, re.IGNORECASE),
                      advice=advice)


# 顺序即提示顺序；每条都说明“为什么危险”，确认弹窗直接展示
RULES: list[DangerRule] = [
    _rule(
        "rm-rf-root", "递归强制删除根目录 / 系统关键目录",
        r"\brm\b[^|;&\n]*\s-(?:[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)[a-z]*\s+"
        r"(?:/|/\*|\*|/bin|/boot|/etc|/lib\d?|/usr|/var|/root|/home|/opt|/sbin)(?:\s|/|\*|$)",
        "rm -rf 作用于根目录或系统关键目录，可能直接摧毁整台机器，且不可恢复。",
    ),
    _rule(
        "rm-rf", "递归强制删除（rm -rf）",
        r"\brm\b[^|;&\n]*\s-(?:[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)[a-z]*\s*\S+",
        "递归强制删除不进回收站、不可恢复，目标填写错误会造成大面积数据丢失。",
    ),
    _rule(
        "mkfs", "格式化文件系统（mkfs）",
        r"\bmkfs(?:\.\w+)?\b[^\n]*\s/dev/\S+",
        "格式化会清空整块设备上的全部数据。",
    ),
    _rule(
        "dd-disk", "向块设备直接写入（dd of=/dev/…）",
        r"\bdd\b[^|;&\n]*\bof=/dev/\S+",
        "dd 直接写裸设备会抹掉分区表与数据，写错目标盘无法挽回。",
    ),
    _rule(
        "fork-bomb", "fork 炸弹（无限自我复制）",
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
        "进程指数级自我复制，会迅速耗尽机器资源导致宕机。",
    ),
    _rule(
        "shutdown-reboot", "关机 / 重启 / 停机",
        r"\b(?:shutdown|reboot|halt|poweroff|init\s+0|init\s+6)\b",
        "目标机器会立即下线，正在提供的服务将中断。",
    ),
    _rule(
        "iptables-flush", "清空防火墙 / 封禁规则",
        r"\b(?:iptables|ip6tables|nft)\b[^\n;|]*\s(?:-F\b|--flush\b|flush\b|delete\s+table)",
        "清空防火墙规则可能让机器暴露在未过滤的网络访问中。",
    ),
    _rule(
        "disk-partition", "磁盘分区操作（fdisk/parted）",
        r"\b(?:fdisk|parted|gdisk|cfdisk)\b",
        "分区表操作失误会导致整块盘的数据不可访问。",
    ),
    _rule(
        "lvm-destructive", "LVM 破坏性操作（pvremove/vgremove/lvremove）",
        r"\b(?:pvremove|vgremove|lvremove|vgreduce|pvmove)\b",
        "移除物理卷/卷组/逻辑卷会销毁其上的全部数据。",
    ),
    _rule(
        "user-delete", "删除用户 / 用户组",
        r"\b(?:userdel|groupdel)\b",
        "删除账号可能导致其文件、定时任务与服务归属丢失。",
    ),
    _rule(
        "chmod-recursive", "递归篡改系统目录权限",
        r"\bchmod\b[^|;&\n]*\s-R\w*\s+(?:[0-7]{3,4})?\s*/(?:\s|$|\*)",
        "对根目录递归改权限会破坏系统程序与 SSH 登录，常见于把自己锁在门外。",
    ),
    _rule(
        "kernel-params", "直接写内核参数 / 破坏 /proc、/sys",
        r"\becho\b[^|;&\n]*>\s*/proc/sys/|"
        r"\becho\b[^|;&\n]*>\s*/sys/|"
        r"\b(?:sysctl\s+-w\b)",
        "直接修改内核运行参数可能立即引发内核恐慌或网络中断。",
    ),
    _rule(
        "kill-all", "成批杀进程（killall/pkill -9/kill -9 -1）",
        r"\b(?:killall|pkill)\b[^|;&\n]*\s-9?\w*\s+\S+|"
        r"\bkill\s+-9\s+-1\b",
        "按名字成批杀进程可能误杀同名的关键服务。",
    ),
    _rule(
        "crontab-wipe", "清空全部定时任务",
        r"\bcrontab\s+-r\b",
        "会删除该用户全部定时任务且没有确认提示。",
    ),
    _rule(
        "download-pipe-shell", "下载内容直接管道给解释器执行",
        r"\b(?:curl|wget)\b[^|;&\n]*\|\s*(?:sudo\s+)?(?:ba|z|da|fi|tc)?sh\b|"
        r"\b(?:curl|wget)\b[^|;&\n]*\s(?:-O\b|--output-document\b)",
        "未经校验地执行远端脚本，脚本内容可随时被替换，等同于把机器交给对方。",
    ),
    _rule(
        "history-wipe", "清除历史与日志（疑似掩盖痕迹）",
        r"\bhistory\s+-c\b[^|;&\n]*|"
        r">\s*~/\.bash_history|"
        r"\b(?:rm\b[^|;&\n]*/var/log/)",
        "清除命令历史或系统日志会破坏事后审计能力。",
    ),
]


def _strip_comments(text: str) -> str:
    """去掉 shell 行注释（不处理引号内的 #），避免规则被注释干扰。"""
    out_lines: list[str] = []
    for line in text.splitlines():
        try:
            tokens = shlex.split(line, comments=True, posix=True)
            out_lines.append(" ".join(tokens))
        except ValueError:
            # 引号不闭合等情况：退化为朴素去注释
            cut = line.find("#")
            out_lines.append(line[:cut] if cut >= 0 else line)
    return "\n".join(out_lines)


def inspect_command(command: str) -> list[dict]:
    """返回命中的危险规则列表：[{key,label,advice,matched}]。"""
    if not command or not command.strip():
        return []
    text = _strip_comments(command)
    hits: list[dict] = []
    seen: set[str] = set()
    for rule in RULES:
        m = rule.pattern.search(text)
        if m and rule.key not in seen:
            seen.add(rule.key)
            hits.append({
                "key": rule.key,
                "label": rule.label,
                "advice": rule.advice,
                "matched": m.group(0).strip()[:120],
            })
    return hits


def is_dangerous(command: str) -> bool:
    return bool(inspect_command(command))
