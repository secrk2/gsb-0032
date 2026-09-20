"""作业、快捷命令批次与操作记录模型。

- :class:`CommandBatch`：一次「快捷命令执行」——同一个操作人、同一条命令，
  同时下发给一台或多台 Linux 主机。危险命令需先确认并记录确认原因。
- :class:`Job`：单台机器上的一次执行。旧的定时/手动作业由 ``kind="job"``
  表示，快捷命令的执行单元为 ``kind="quick"``。完整输出归档在 ``output``
  字段（Redis 仅作实时缓冲，过期/重启后仍可按机器和时间回看）。
- :class:`AuditLog`：平台操作记录，供仪表盘与事后追溯。
"""
from __future__ import annotations

from django.db import models

from hosts.models import Host


class JobStatus(models.TextChoices):
    RUNNING = "running", "执行中"
    SUCCESS = "success", "成功"
    FAILED = "failed", "失败"


class JobKind(models.TextChoices):
    JOB = "job", "普通作业"
    QUICK = "quick", "快捷命令"


class ErrorCategory(models.TextChoices):
    """执行异常的细分类别，便于前端分别提示「超时 / 认证失败 / …」。

    命令本身返回非零退出码不属于「连接异常」，用 status=failed + exit_code 表达。
    """

    TIMEOUT = "timeout", "连接超时"
    AUTH = "auth", "认证失败"
    CONFIG = "config", "凭据未配置"
    CONNECTION = "connection", "无法连接"
    PRIVILEGE = "privilege", "权限不足"
    INTERRUPTED = "interrupted", "已中断"
    REMOTE = "remote", "命令返回非零退出码"
    UNKNOWN = "unknown", "其它异常"


class CommandBatch(models.Model):
    """一次快捷命令下发：一条命令、多台机器、一个操作人。"""

    actor = models.CharField("操作人", max_length=64, default="admin")
    command = models.TextField("命令")
    host_count = models.PositiveIntegerField("目标机器数", default=0)
    dangerous = models.BooleanField("命中危险规则", default=False)
    confirm_reason = models.CharField("确认原因", max_length=512, blank=True, default="")
    confirmed_at = models.DateTimeField("确认时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "命令批次"
        verbose_name_plural = "命令批次"
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"#{self.pk} {self.command[:40]} ({self.host_count} 台)"


class Job(models.Model):
    name = models.CharField("作业名称", max_length=255)
    kind = models.CharField(
        "执行类型", max_length=16, choices=JobKind.choices, default=JobKind.JOB
    )
    batch = models.ForeignKey(
        CommandBatch, null=True, blank=True, on_delete=models.CASCADE,
        related_name="jobs", verbose_name="所属批次",
    )
    host = models.ForeignKey(
        Host, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="jobs", verbose_name="目标主机",
    )
    host_name_snapshot = models.CharField("主机名快照", max_length=128, default="")
    command = models.TextField("命令/脚本", blank=True, default="")
    status = models.CharField(
        "状态", max_length=16, choices=JobStatus.choices, default=JobStatus.RUNNING
    )
    exit_code = models.IntegerField("退出码", null=True, blank=True)
    # 连接/执行异常的细分类别与信息（命令返回非零不在此列，看 exit_code）
    error_category = models.CharField(
        "异常类别", max_length=16, choices=ErrorCategory.choices,
        blank=True, default="",
    )
    error_message = models.TextField("异常信息", blank=True, default="")
    interrupted = models.BooleanField("用户中断", default=False)
    # 完整输出归档：执行结束写入，历史回看不再依赖 Redis
    output = models.TextField("完整输出", blank=True, default="")
    output_truncated = models.BooleanField("输出曾被截断", default=False)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    finished_at = models.DateTimeField("结束时间", null=True, blank=True)

    class Meta:
        verbose_name = "作业"
        verbose_name_plural = "作业"
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["kind", "-created_at"]),
            # 事后按机器 + 时间查执行记录
            models.Index(fields=["host", "-created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} [{self.status}]"

    @property
    def stream_key(self) -> str:
        """Redis 输出流（实时缓冲）。"""
        return f"zhiyue:job:{self.pk}:output"

    @property
    def control_channel(self) -> str:
        """Redis pub/sub 控制频道（用于跨 worker 中断正在执行的作业）。"""
        return f"zhiyue:job:{self.pk}:control"

    @property
    def output_key(self) -> str:
        # 兼容旧代码引用
        return self.stream_key


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
