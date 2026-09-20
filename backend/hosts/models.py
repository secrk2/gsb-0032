"""主机与目录领域模型。

树结构约束：
- :class:`Directory` 自关联，空 parent 即根目录；同级 (parent, name) 唯一。
- 主机与目录是多对多：一台主机可同时挂在多个目录下。
- 移动/改名的防环与同级重名校验放在 :mod:`hosts.services`，
  视图层和管理命令都走同一入口，保证树永远不会被写断。
"""
from __future__ import annotations

from django.db import models

from .crypto import decrypt_secret, encrypt_secret


class OS(models.TextChoices):
    LINUX = "linux", "Linux"
    WINDOWS = "windows", "Windows"


class ConnectType(models.TextChoices):
    SSH = "ssh", "SSH"
    RDP = "rdp", "RDP"


class HostStatus(models.TextChoices):
    UNKNOWN = "unknown", "未知"
    ONLINE = "online", "在线"
    OFFLINE = "offline", "离线"


class Directory(models.Model):
    """自定义目录节点，无限层级。"""

    name = models.CharField("名称", max_length=128)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="children",
        on_delete=models.CASCADE,
        verbose_name="父目录",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "目录"
        verbose_name_plural = "目录"
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name"], name="uniq_dir_sibling_name"
            )
        ]
        indexes = [models.Index(fields=["parent"])]

    def __str__(self) -> str:
        return self.name

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    def ancestor_ids(self) -> set[int]:
        """沿 parent 链向上收集所有祖先 id（含自身），用于防环判断。"""
        ids: set[int] = set()
        node = self
        seen = set()
        while node is not None and node.id not in seen:
            seen.add(node.id)
            ids.add(node.id)
            node = node.parent
        return ids


class Host(models.Model):
    """一台被纳管的机器。口令/密钥加密存储，接口永不回传明文。"""

    name = models.CharField("主机名", max_length=128)
    address = models.CharField("地址", max_length=255)
    port = models.PositiveIntegerField("端口")
    os_type = models.CharField(
        "操作系统", max_length=16, choices=OS.choices, default=OS.LINUX
    )
    connect_type = models.CharField(
        "连接方式", max_length=16, choices=ConnectType.choices
    )
    status = models.CharField(
        "在线状态", max_length=16, choices=HostStatus.choices, default=HostStatus.UNKNOWN
    )
    status_checked_at = models.DateTimeField("状态探测时间", null=True, blank=True)

    username = models.CharField("登录账号", max_length=128, default="")
    _password = models.BinaryField("登录口令(密文)", null=True, blank=True, editable=False,
                                   db_column="password_encrypted")
    _private_key = models.BinaryField("私钥(密文)", null=True, blank=True, editable=False,
                                      db_column="private_key_encrypted")

    directories = models.ManyToManyField(
        Directory, through="DirectoryHost", related_name="hosts", blank=True
    )

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "主机"
        verbose_name_plural = "主机"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["os_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.address})"

    # --- 凭据的加密封装 -------------------------------------------------
    def set_password(self, raw: str) -> None:
        self._password = encrypt_secret(raw) if raw else b""

    def get_password(self) -> str:
        return decrypt_secret(bytes(self._password) if self._password else b"")

    def set_private_key(self, raw: str) -> None:
        self._private_key = encrypt_secret(raw) if raw else b""

    def get_private_key(self) -> str:
        return decrypt_secret(bytes(self._private_key) if self._private_key else b"")

    @property
    def has_password(self) -> bool:
        return bool(self._password)

    @property
    def has_private_key(self) -> bool:
        return bool(self._private_key)


class DirectoryHost(models.Model):
    """主机 <-> 目录的挂接关系（多对多），带目录内排序。"""

    directory = models.ForeignKey(
        Directory, on_delete=models.CASCADE, related_name="memberships"
    )
    host = models.ForeignKey(Host, on_delete=models.CASCADE, related_name="memberships")
    position = models.IntegerField("目录内排序", default=0)
    created_at = models.DateTimeField("挂载时间", auto_now_add=True)

    class Meta:
        verbose_name = "目录-主机挂接"
        verbose_name_plural = "目录-主机挂接"
        constraints = [
            models.UniqueConstraint(
                fields=["directory", "host"], name="uniq_dir_host"
            )
        ]
        indexes = [models.Index(fields=["directory", "position"])]

    def __str__(self) -> str:
        return f"{self.directory_id} -> {self.host_id}"
