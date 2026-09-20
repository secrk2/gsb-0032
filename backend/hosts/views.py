"""主机与目录的 HTTP 接口。

重点接口：
- ``GET  /api/tree``                整棵目录树（目录嵌套 + 每级主机）
- ``POST /api/directories``         新建目录
- ``POST /api/directories/{id}/move``    拖拽移动（服务端二次防环）
- ``PATCH /api/directories/{id}/``       改名
- ``DELETE /api/directories/{id}/``      删除（子树级联，主机保留）
- ``GET/POST/PATCH/DELETE /api/hosts``   主机增删改查（凭据只写不读）
- ``POST /api/hosts/{id}/attach``、``detach`` 多目录挂接/摘除
- ``POST /api/hosts/{id}/check``         立即探测一次在线状态
- ``GET  /api/events``                   SSE 实时事件流
"""
from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from ops.services import log_action
from ops.models import AuditResult
from zhiyue.events import event_stream, publish

from .models import Directory, DirectoryHost, Host
from . import services
from .serializers import (
    DirectorySerializer,
    HostSerializer,
)
from .services import TreeError


# ---------------------------------------------------------------------------
# 目录树
# ---------------------------------------------------------------------------
def _serialize_tree() -> list[dict]:
    """一次查询拼出完整嵌套树，避免 N+1。"""
    directories = list(Directory.objects.all().order_by("name"))
    links = DirectoryHost.objects.select_related("host").order_by("position")

    host_serializer = HostSerializer(
        (link.host for link in links), many=True, context={"nested": True}
    )
    # 同一个主机可能挂在多个目录下，需要按目录分组输出
    host_payload = host_serializer.data
    hosts_by_dir: dict[int, list[dict]] = {}
    for link, payload in zip(links, host_payload):
        hosts_by_dir.setdefault(link.directory_id, []).append(payload)

    nodes: dict[int, dict] = {}
    for d in directories:
        nodes[d.pk] = {
            "id": d.pk,
            "name": d.name,
            "parent_id": d.parent_id,
            "created_at": d.created_at,
            "updated_at": d.updated_at,
            "children": [],
            "hosts": hosts_by_dir.get(d.pk, []),
        }

    roots: list[dict] = []
    for d in directories:
        node = nodes[d.pk]
        if d.parent_id and d.parent_id in nodes:
            nodes[d.parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


class TreeView(APIView):
    def get(self, request):
        return Response(_serialize_tree())


class DirectoryViewSet(viewsets.ViewSet):
    def list(self, request):
        return Response(DirectorySerializer(Directory.objects.all(), many=True).data)

    def create(self, request):
        serializer = DirectorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parent = serializer.validated_data.get("parent")
        try:
            directory = services.create_directory(
                serializer.validated_data["name"], parent
            )
        except TreeError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        log_action("create", "directory", directory.name,
                   detail=f"父目录: {parent.name if parent else '根层'}")
        publish("tree.changed", {"reason": "directory_created", "id": directory.pk})
        return Response(DirectorySerializer(directory).data,
                        status=status.HTTP_201_CREATED)

    def destroy(self, request, pk=None):
        directory = get_object_or_404(Directory, pk=pk)
        name = directory.name
        services.delete_directory(directory)
        log_action("delete", "directory", name, detail="子树一并删除，挂接主机保留")
        publish("tree.changed", {"reason": "directory_deleted", "id": pk})
        return Response(status=status.HTTP_204_NO_CONTENT)

    def partial_update(self, request, pk=None):
        directory = get_object_or_404(Directory, pk=pk)
        new_name = (request.data or {}).get("name")
        try:
            services.rename_directory(directory, new_name or "")
        except TreeError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        log_action("rename", "directory", directory.name)
        publish("tree.changed", {"reason": "directory_renamed", "id": directory.pk})
        return Response(DirectorySerializer(directory).data)

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        """拖拽：{target_id: int|null}。后端再次防环，前端拦截只是第一道。"""
        directory = get_object_or_404(Directory, pk=pk)
        target_id = (request.data or {}).get("target_id")
        new_parent = None
        if target_id is not None:
            new_parent = get_object_or_404(Directory, pk=target_id)
        try:
            services.move_directory(directory, new_parent)
        except Directory.DoesNotExist:
            return Response({"detail": "目标目录不存在"},
                            status=status.HTTP_404_NOT_FOUND)
        except TreeError as exc:
            log_action("move", "directory", directory.name,
                       detail=str(exc), result=AuditResult.FAILED)
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        log_action("move", "directory", directory.name,
                   detail=f"移动到: {new_parent.name if new_parent else '根层'}")
        publish("tree.changed", {"reason": "directory_moved", "id": directory.pk})
        return Response(DirectorySerializer(directory).data)


# ---------------------------------------------------------------------------
# 主机
# ---------------------------------------------------------------------------
class HostViewSet(viewsets.ViewSet):
    queryset = Host.objects.all()

    def list(self, request):
        hosts = Host.objects.all().prefetch_related("directories")
        return Response(HostSerializer(hosts, many=True).data)

    def retrieve(self, request, pk=None):
        host = get_object_or_404(Host, pk=pk)
        return Response(HostSerializer(host).data)

    def create(self, request):
        serializer = HostSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        host = serializer.save()
        log_action("create", "host", host.name,
                   detail=f"{host.os_type}/{host.connect_type} {host.address}:{host.port}")
        publish("tree.changed", {"reason": "host_created", "id": host.pk})
        return Response(HostSerializer(host).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        host = get_object_or_404(Host, pk=pk)
        serializer = HostSerializer(host, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        host = serializer.save()
        log_action("update", "host", host.name)
        publish("tree.changed", {"reason": "host_updated", "id": host.pk})
        return Response(HostSerializer(host).data)

    def destroy(self, request, pk=None):
        host = get_object_or_404(Host, pk=pk)
        name = host.name
        host.delete()
        log_action("delete", "host", name)
        publish("tree.changed", {"reason": "host_deleted", "id": pk})
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def attach(self, request, pk=None):
        """把主机挂到目录（可重复挂多个目录）。"""
        host = get_object_or_404(Host, pk=pk)
        directory = get_object_or_404(
            Directory, pk=(request.data or {}).get("directory_id")
        )
        position = (request.data or {}).get("position")
        services.attach_host(host, directory, position)
        log_action("attach", "host", host.name, detail=f"挂到目录「{directory.name}」")
        publish("tree.changed", {"reason": "host_attached", "id": host.pk})
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def detach(self, request, pk=None):
        host = get_object_or_404(Host, pk=pk)
        directory = get_object_or_404(
            Directory, pk=(request.data or {}).get("directory_id")
        )
        services.detach_host(host, directory)
        log_action("detach", "host", host.name, detail=f"从目录「{directory.name}」摘除")
        publish("tree.changed", {"reason": "host_detached", "id": host.pk})
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def check(self, request, pk=None):
        """立即触发一次在线探测（同步执行，便于界面上立刻验证）。"""
        host = get_object_or_404(Host, pk=pk)
        from .probe import probe_host

        result = probe_host(host)
        return Response(result)


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------
async def events_view(request):
    from django.http import StreamingHttpResponse

    last_id = request.headers.get("Last-Event-ID")
    response = StreamingHttpResponse(
        event_stream(last_id), content_type="text/event-stream"
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
