"""目录树写操作的唯一入口：改名、移动、删除、挂接主机。

所有可能「把树弄断」的操作都在此校验并抛 :class:`TreeError`，
视图 / 管理命令 / 后续作业系统复用同一套规则：

1. 目录不能移动到自身或自己的任意后代之下（防环）。
2. 同级目录不能重名。
3. 主机可挂多个目录，但同一目录下不能重复挂接。
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import Max

from .models import Directory, DirectoryHost, Host


class TreeError(ValueError):
    """违反目录树不变量时抛出。"""


@transaction.atomic
def create_directory(name: str, parent: Directory | None = None) -> Directory:
    name = (name or "").strip()
    if not name:
        raise TreeError("目录名称不能为空")
    if Directory.objects.filter(parent=parent, name=name).exists():
        raise TreeError(f"同级下已存在名为「{name}」的目录")
    return Directory.objects.create(name=name, parent=parent)


@transaction.atomic
def rename_directory(directory: Directory, new_name: str) -> Directory:
    new_name = (new_name or "").strip()
    if not new_name:
        raise TreeError("目录名称不能为空")
    if Directory.objects.filter(parent=directory.parent, name=new_name).exclude(
        pk=directory.pk
    ).exists():
        raise TreeError(f"同级下已存在名为「{new_name}」的目录")
    directory.name = new_name
    directory.save(update_fields=["name", "updated_at"])
    return directory


@transaction.atomic
def move_directory(directory: Directory, new_parent: Directory | None) -> Directory:
    """把 *directory* 移动到 *new_parent* 下（None 表示移动到根层）。"""
    if new_parent is not None:
        # 新父节点不能是自己，也不能位于自己的子树中——否则成环。
        if new_parent.pk in collect_subtree_ids(directory):
            if new_parent.pk == directory.pk:
                raise TreeError("不能把目录移动到它自身下面")
            raise TreeError("不能把目录移动到它自己的子目录下面")
        if Directory.objects.filter(parent=new_parent, name=directory.name).exclude(
            pk=directory.pk
        ).exists():
            raise TreeError(f"目标目录下已存在同名目录「{directory.name}」")
    else:
        if Directory.objects.filter(parent__isnull=True, name=directory.name).exclude(
            pk=directory.pk
        ).exists():
            raise TreeError(f"根层已存在同名目录「{directory.name}」")

    directory.parent = new_parent
    directory.save(update_fields=["parent", "updated_at"])
    return directory


def collect_subtree_ids(directory: Directory) -> list[int]:
    """返回含自身在内的整棵子树 id（迭代展开，避免深层递归）。"""
    ids = [directory.pk]
    frontier = [directory.pk]
    while frontier:
        kids = list(
            Directory.objects.filter(parent_id__in=frontier).values_list("pk", flat=True)
        )
        ids.extend(kids)
        frontier = kids
    return ids


@transaction.atomic
def delete_directory(directory: Directory) -> None:
    """删除目录。子目录级联删除；主机只是被摘掉挂接，主机本身保留。"""
    DirectoryHost.objects.filter(
        directory__in=collect_subtree_ids(directory)
    ).delete()
    directory.delete()


@transaction.atomic
def attach_host(host: Host, directory: Directory, position: int | None = None) -> DirectoryHost:
    """把主机挂到目录下；已挂接则直接返回原关系（幂等）。"""
    existing = DirectoryHost.objects.filter(directory=directory, host=host).first()
    if existing:
        return existing
    if position is None:
        agg = DirectoryHost.objects.filter(directory=directory).aggregate(
            max_pos=Max("position")
        )
        position = (agg["max_pos"] or 0) + 1
    return DirectoryHost.objects.create(directory=directory, host=host, position=position)


@transaction.atomic
def detach_host(host: Host, directory: Directory) -> None:
    DirectoryHost.objects.filter(directory=directory, host=host).delete()


@transaction.atomic
def reorder_host(directory: Directory, host: Host, position: int) -> DirectoryHost:
    link, _ = DirectoryHost.objects.get_or_create(directory=directory, host=host)
    link.position = position
    link.save(update_fields=["position"])
    return link
