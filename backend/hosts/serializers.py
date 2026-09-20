"""主机与目录的 DRF 序列化器。

安全约束：password / private_key 只写不读，接口输出只有
has_password / has_private_key 布尔位，密文与明文均不下发前端。
"""
from __future__ import annotations

from rest_framework import serializers

from .models import ConnectType, Directory, DirectoryHost, Host, OS


class DirectorySerializer(serializers.ModelSerializer):
    parent_id = serializers.PrimaryKeyRelatedField(
        source="parent",
        queryset=Directory.objects.all(),
        allow_null=True,
        required=False,
    )
    child_count = serializers.SerializerMethodField()
    host_count = serializers.SerializerMethodField()

    class Meta:
        model = Directory
        fields = [
            "id",
            "name",
            "parent_id",
            "child_count",
            "host_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_child_count(self, obj: Directory) -> int:
        return obj.children.count()

    def get_host_count(self, obj: Directory) -> int:
        return obj.memberships.count()


class DirectoryMoveSerializer(serializers.Serializer):
    """拖拽移动目录的入参。"""

    target_id = serializers.IntegerField(
        allow_null=True, help_text="目标父目录 id；null=根层"
    )


class DirectoryRenameSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)


class HostSerializer(serializers.ModelSerializer):
    os_type = serializers.ChoiceField(choices=OS.choices)
    connect_type = serializers.ChoiceField(choices=ConnectType.choices)
    port = serializers.IntegerField(min_value=1, max_value=65535)

    # 只写：接收明文凭据，服务端加密入库
    password = serializers.CharField(
        write_only=True, required=False, allow_blank=True, allow_null=True
    )
    private_key = serializers.CharField(
        write_only=True, required=False, allow_blank=True, allow_null=True
    )
    # 只读：告诉前端是否已配置凭据
    has_password = serializers.BooleanField(read_only=True)
    has_private_key = serializers.BooleanField(read_only=True)

    # 挂接到哪些目录（多对多）
    directory_ids = serializers.PrimaryKeyRelatedField(
        source="directories",
        many=True,
        queryset=Directory.objects.all(),
        required=False,
    )

    class Meta:
        model = Host
        fields = [
            "id",
            "name",
            "address",
            "port",
            "os_type",
            "connect_type",
            "status",
            "status_checked_at",
            "username",
            "password",
            "private_key",
            "has_password",
            "has_private_key",
            "directory_ids",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "status", "status_checked_at", "created_at", "updated_at"]

    def validate(self, attrs):
        os_type = attrs.get("os_type", getattr(self.instance, "os_type", OS.LINUX))
        connect_type = attrs.get(
            "connect_type", getattr(self.instance, "connect_type", None)
        )
        if connect_type == ConnectType.RDP and os_type != OS.WINDOWS:
            raise serializers.ValidationError("RDP 连接方式只能用于 Windows 主机")
        if connect_type == ConnectType.SSH and os_type != OS.LINUX:
            raise serializers.ValidationError("SSH 连接方式只能用于 Linux 主机")
        return attrs

    def _apply_credentials(self, host: Host, validated_data: dict) -> None:
        password = validated_data.pop("password", None)
        private_key = validated_data.pop("private_key", None)
        # 仅当请求里显式带了字段才更新，避免编辑其它字段时清空凭据
        if password is not None:
            host.set_password(password)
        if private_key is not None:
            host.set_private_key(private_key)

    def create(self, validated_data):
        directories = validated_data.pop("directories", [])
        host = Host()
        self._apply_credentials(host, validated_data)
        for attr, value in validated_data.items():
            setattr(host, attr, value)
        host.save()
        if directories:
            for position, directory in enumerate(directories, start=1):
                DirectoryHost.objects.create(
                    directory=directory, host=host, position=position
                )
        return host

    def update(self, host: Host, validated_data):
        directories = validated_data.pop("directories", None)
        self._apply_credentials(host, validated_data)
        for attr, value in validated_data.items():
            setattr(host, attr, value)
        host.save()
        if directories is not None:
            # 全量覆盖挂接关系
            DirectoryHost.objects.filter(host=host).delete()
            for position, directory in enumerate(directories, start=1):
                DirectoryHost.objects.create(
                    directory=directory, host=host, position=position
                )
        return host


class HostAttachSerializer(serializers.Serializer):
    """把主机挂到某个目录（一台机器可挂多个目录）。"""

    host_id = serializers.IntegerField()
    directory_id = serializers.IntegerField()
    position = serializers.IntegerField(required=False, allow_null=True)
