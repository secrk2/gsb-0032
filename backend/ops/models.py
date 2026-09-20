"""作业、命令批次与操作记录模型。

- :class:`CommandBatch`：一次「快捷命令」派发，可能同时下发到多台主机；
  每台主机对应一条 :class:`Job`，各自独立记录状态 / 退出码 / 输出 / 连接错误。
- :class:`Job`：在一台主机上执行的一条命令。完整输出落库（``output`` 长文本，
  不截断），实时增量同时经 SSE 广播。
- :class:`AuditLog`：平台操作记录（含危险命令确认原因）。
"""
from __future__ import annotations

from django.db import models

from hosts.models import Host


class JobStatus(models.TextChoices):
    RUNNING = "running", "执行中"
    SUCCESS = "success", "成功"
    FAILED = "failed", "失败"
    ABORTED = "aborted", "已中断"


class ErrorKind(models.TextChoices):
    """连接 / 执行失败的分类，前端按分类给出明确的中文说明，而不是一句“连不上”。"""

    TIMEOUT = "timeout", "连接超时"
    AUTH_FAILED = "auth_failed", "认证失败"
    CONNECTION_REFUSED = "connection_refused", "连接被拒绝"
    HOST_UNREACHABLE = "host_unreachable", "主机不可达"
    CONNECTION_RESET = "connection_reset", "连接被重置"
    DNS_ERROR = "dns_error", "地址解析失败"
    NO_CREDENTIAL = "no_credential", "未配置凭据"
    ABORTED = "aborted", "用户中断"
    SSH_ERROR = "ssh_error", "SSH 协议错误"
    WORKER_RESTART = "worker_restart", "执行服务重启"
    ERROR = "error", "其他错误"


class CommandBatch(models.Model):
    """一次快捷命令派发：一条命令 -> 多台主机（多条 Job）。"""

    name = models.CharField("批次名称", max_length=255, default="快捷命令")
    command = models.TextField("命令/脚本")
    actor = models.CharField("操作人", max_length=64, default="admin")
    dangerous = models.BooleanField("命中危险命令", default=False)
    confirm_reason = models.TextField("危险命令确认原因", blank=True, default="")
    host_count = models.PositiveIntegerField("目标主机数", default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "命令批次"
        verbose_name_plural = "命令批次"
        indexes = [models.Index(fields=["-created_at"])]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} -> {self.host_count} 台 [{self.created_at:%Y-%m-%d %H:%M}]"


class Job(models.Model):
    batch = models.ForeignKey(
        CommandBatch, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="jobs", verbose_name="所属命令批次",
    )
    name = models.CharField("作业名称", max_length=255)
    host = models.ForeignKey(
        Host, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="jobs", verbose_name="目标主机",
    )
    host_name_snapshot = models.CharField("主机名快照", max_length=128, default="")
    command = models.TextField("命令/脚本", blank=True, default="")
    actor = models.CharField("操作人", max_length=64, default="admin")
    status = models.CharField(
        "状态", max_length=16, choices=JobStatus.choices, default=JobStatus.RUNNING
    )
    exit_code = models.IntegerField("退出码", null=True, blank=True)

    # 完整输出落库（MySQL longtext），实时增量走 SSE；事后按机器/时间可回放全部内容
    output = models.TextField("完整输出", blank=True, default="")
    error_kind = models.CharField(
        "错误分类", max_length=32, choices=ErrorKind.choices, blank=True, default=""
    )
    error_message = models.TextField("错误详情", blank=True, default="")

    # 危险命令及确认留痕
    dangerous = models.BooleanField("命中危险命令", default=False)
    confirm_reason = models.TextField("危险命令确认原因", blank=True, default="")

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    finished_at = models.DateTimeField("结束时间", null=True, blank=True)

    class Meta:
        verbose_name = "作业"
        verbose_name_plural = "作业"
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["host", "-created_at"]),
            models.Index(fields=["batch"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} [{self.status}]"

    @property
    def output_key(self) -> str:
        """兼容旧逻辑的 Redis 键名（输出已改为落库，保留仅为兼容）。"""
        return f"zhiyue:job:{self.pk}:output"

    @property
    def abort_key(self) -> str:
        return f"zhiyue:job:{self.pk}:abort"


class AuditResult(models.TextChoices):
    SUCCESS = "success", "成功"
    FAILED = "failed", "失败"
    INFO = "info", "信息"


class AuditLog(models.Model):
    actor = models.CharField("操作人", max_length=64, default="admin")
    action = models.CharField("动作", max_length=64)
    target_type = models.CharField("对象类型", max_length=32)
    target_name = models.CharField("对象名称", max_length=255, blank=True, default="")
    detail = models.CharField("详情", max_length=512, blank=True, default="")
    result = models.CharField(
        "结果", max_length=16, choices=AuditResult.choices, default=AuditResult.SUCCESS
    )
    created_at = models.DateTimeField("时间", auto_now_add=True)

    class Meta:
        verbose_name = "操作记录"
        verbose_name_plural = "操作记录"
        indexes = [models.Index(fields=["-created_at"])]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.action} {self.target_type}:{self.target_name}"
