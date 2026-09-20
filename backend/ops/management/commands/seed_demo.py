"""写入演示数据：多级目录树 + 一批主机（含多目录挂接）+ 今日作业 + 操作记录。

幂等：以名称作为自然键重复执行不会产生重复数据。

compose 网络里三台模拟客户端的地址：
- linux-client-1:22 / linux-client-2:22  （ops / Linux@2026）
- windows-client:3389                    （admin / Windows@2026）
其余主机使用不可达的占位地址，仪表盘上会呈现在线/离线混合状态。
"""
from __future__ import annotations

import os

from django.core.management.base import BaseCommand
from django.utils import timezone

from hosts.models import ConnectType, Directory, DirectoryHost, Host, OS
from hosts.services import attach_host
from ops.models import AuditLog, Job, JobStatus
from ops.services import log_action

LINUX_USER = os.environ.get("SEED_LINUX_USER", "ops")
LINUX_PASSWORD = os.environ.get("SEED_LINUX_PASSWORD", "Linux@2026")
WIN_USER = os.environ.get("SEED_WINDOWS_USER", "admin")
WIN_PASSWORD = os.environ.get("SEED_WINDOWS_PASSWORD", "Windows@2026")


# (目录路径, ) —— 用路径元组描述无限层级
DIRECTORY_TREE = [
    ("生产环境",),
    ("生产环境", "核心业务"),
    ("生产环境", "核心业务", "数据库"),
    ("生产环境", "核心业务", "应用集群"),
    ("生产环境", "网关层"),
    ("预发环境",),
    ("测试环境",),
    ("测试环境", "功能测试"),
    ("测试环境", "Windows 工位"),
    ("基础设施",),
    ("基础设施", "监控"),
    ("基础设施", "跳板与巡检"),
]

# (名称, 地址, 端口, 系统, 连接方式, 账号, 口令, [挂载目录路径...])
HOSTS = [
    ("web-prod-01", "linux-client-1", 22, OS.LINUX, ConnectType.SSH,
     LINUX_USER, LINUX_PASSWORD,
     [("生产环境", "核心业务", "应用集群"), ("基础设施", "跳板与巡检")]),
    ("web-prod-02", "linux-client-2", 22, OS.LINUX, ConnectType.SSH,
     LINUX_USER, LINUX_PASSWORD,
     [("生产环境", "核心业务", "应用集群")]),
    ("db-prod-01", "10.10.1.11", 22, OS.LINUX, ConnectType.SSH,
     "dba", "Dba@2026", [("生产环境", "核心业务", "数据库")]),
    ("db-prod-02", "10.10.1.12", 22, OS.LINUX, ConnectType.SSH,
     "dba", "Dba@2026", [("生产环境", "核心业务", "数据库")]),
    ("cache-prod-01", "10.10.2.21", 22, OS.LINUX, ConnectType.SSH,
     "ops", "Ops@2026", [("生产环境", "核心业务")]),
    ("gw-prod-01", "10.10.0.1", 22, OS.LINUX, ConnectType.SSH,
     "ops", "Ops@2026", [("生产环境", "网关层")]),
    ("staging-app-01", "10.20.1.10", 22, OS.LINUX, ConnectType.SSH,
     "ops", "Ops@2026", [("预发环境",)]),
    ("test-linux-01", "10.30.1.10", 22, OS.LINUX, ConnectType.SSH,
     "qa", "Qa@2026", [("测试环境", "功能测试")]),
    ("win-rdp-01", "windows-client", 3389, OS.WINDOWS, ConnectType.RDP,
     WIN_USER, WIN_PASSWORD, [("测试环境", "Windows 工位")]),
    ("win-rdp-02", "10.30.2.11", 3389, OS.WINDOWS, ConnectType.RDP,
     WIN_USER, WIN_PASSWORD, [("测试环境", "Windows 工位")]),
    ("monitor-01", "10.40.0.10", 22, OS.LINUX, ConnectType.SSH,
     "ops", "Ops@2026", [("基础设施", "监控")]),
]

JOBS = [
    ("巡检磁盘使用率", "web-prod-01", "df -h", JobStatus.SUCCESS, 0),
    ("巡检系统负载", "web-prod-02", "uptime", JobStatus.SUCCESS, 0),
    ("备份数据库", "db-prod-01", "/usr/local/bin/backup.sh", JobStatus.FAILED, 1),
    ("采集 Nginx 指标", "gw-prod-01", "tail -n 200 /var/log/nginx/access.log",
     JobStatus.SUCCESS, 0),
]

AUDITS = [
    ("login", "user", "admin", "登录平台", "success"),
    ("create", "host", "web-prod-01", "纳管到「应用集群」", "success"),
    ("move", "directory", "数据库", "移动到「核心业务」下", "success"),
    ("run", "job", "备份数据库", "主机「db-prod-01」执行失败，退出码 1", "failed"),
    ("attach", "host", "web-prod-01", "挂到目录「跳板与巡检」", "success"),
]


class Command(BaseCommand):
    help = "写入演示用的目录树、主机、作业与操作记录（幂等）"

    def handle(self, *args, **options):
        dirs_by_path: dict[tuple[str, ...], Directory] = {}

        for path in DIRECTORY_TREE:
            parent = dirs_by_path.get(path[:-1]) if len(path) > 1 else None
            directory, created = Directory.objects.get_or_create(
                parent=parent, name=path[-1]
            )
            dirs_by_path[path] = directory

        host_objs: dict[str, Host] = {}
        for (name, address, port, os_type, connect_type,
             username, password, mounts) in HOSTS:
            host, created = Host.objects.get_or_create(
                name=name,
                defaults={
                    "address": address, "port": port, "os_type": os_type,
                    "connect_type": connect_type, "username": username,
                },
            )
            if created:
                host.set_password(password)
                host.save()
            host_objs[name] = host
            for path in mounts:
                attach_host(host, dirs_by_path[path])

        # 今日作业（含一条失败）
        for job_name, host_name, command, job_status, exit_code in JOBS:
            host = host_objs.get(host_name)
            Job.objects.get_or_create(
                name=job_name,
                defaults={
                    "host": host,
                    "host_name_snapshot": host_name,
                    "command": command,
                    "status": job_status,
                    "exit_code": exit_code,
                    "started_at": timezone.now(),
                    "finished_at": timezone.now(),
                },
            )

        for action, target_type, target_name, detail, result in AUDITS:
            if not AuditLog.objects.filter(
                action=action, target_name=target_name, detail=detail
            ).exists():
                log_action(action, target_type, target_name, detail=detail,
                           result=result)

        self.stdout.write(self.style.SUCCESS(
            f"种子完成：{len(DIRECTORY_TREE)} 个目录，{len(HOSTS)} 台主机，"
            f"{len(JOBS)} 个作业，{len(AUDITS)} 条操作记录"
        ))
