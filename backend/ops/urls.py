"""作业 / 仪表盘 / 快捷命令路由。"""
from django.urls import path

from .views import (
    BatchViewSet,
    CommandInspectView,
    CommandRunView,
    DashboardView,
    JobViewSet,
)

urlpatterns = [
    path("dashboard", DashboardView.as_view()),

    # 快捷命令
    path("commands/inspect", CommandInspectView.as_view()),
    path("commands/run", CommandRunView.as_view()),

    # 命令批次
    path("batches", BatchViewSet.as_view({"get": "list"})),
    path("batches/<int:pk>/abort", BatchViewSet.as_view({"post": "abort"})),

    # 作业历史 / 输出 / 中断 / 旧版同步执行
    path("jobs", JobViewSet.as_view({"get": "list"})),
    path("jobs/run", JobViewSet.as_view({"post": "run"})),
    path("jobs/<int:pk>/output", JobViewSet.as_view({"get": "output"})),
    path("jobs/<int:pk>/abort", JobViewSet.as_view({"post": "abort"})),
]
