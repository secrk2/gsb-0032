"""作业与操作记录模型。

- :class:`Job`：一次在主机上执行的作业（后续由 asyncssh / RDP 通道驱动），
  输出缓冲走 Redis（key 见 ``job_output_key``）。
- :class:`AuditLog`：平台最近操作记录，供仪表盘展示。
"""
from __future__ import annotations

from django.db import models

from hosts.models import Host


class JobStatus(models.TextChoices):
    RUNNING = "running", "执行中"
    SUCCESS = "success", "成功"
    FAILED = "failed", "失败"


class Job(models.Model):
    name = models.CharField("作业名称", max_length=255)
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
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    finished_at = models.DateTimeField("结束时间", null=True, blank=True)

    class Meta:
        verbose_name = "作业"
        verbose_name_plural = "作业"
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} [{self.status}]"

    @property
    def output_key(self) -> str:
        return f"zhiyue:job:{self.pk}:output"


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
