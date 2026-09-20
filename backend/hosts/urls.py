"""主机与目录路由。"""
from django.urls import path

from .views import (
    DirectoryViewSet,
    HostViewSet,
    TreeView,
    events_view,
)

_dir = DirectoryViewSet.as_view({"get": "list", "post": "create"})
_dir_detail = DirectoryViewSet.as_view(
    {"patch": "partial_update", "delete": "destroy"}
)
_host = HostViewSet.as_view({"get": "list", "post": "create"})
_host_detail = HostViewSet.as_view(
    {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
)

urlpatterns = [
    path("tree", TreeView.as_view()),
    path("events", events_view),
    path("directories", _dir),
    path("directories/<int:pk>", _dir_detail),
    path("directories/<int:pk>/move", DirectoryViewSet.as_view({"post": "move"})),
    path("hosts", _host),
    path("hosts/<int:pk>", _host_detail),
    path("hosts/<int:pk>/attach", HostViewSet.as_view({"post": "attach"})),
    path("hosts/<int:pk>/detach", HostViewSet.as_view({"post": "detach"})),
    path("hosts/<int:pk>/check", HostViewSet.as_view({"post": "check"})),
]
