"""作业 / 仪表盘 / 快捷命令路由。"""
from django.urls import path

from .views import DashboardView, JobViewSet
from .quick import (
    BatchCancelView,
    BatchDetailView,
    HistoryView,
    JobCancelView,
    JobOutputView,
    QuickCheckView,
    QuickRunView,
)

urlpatterns = [
    path("dashboard", DashboardView.as_view()),
    path("jobs", JobViewSet.as_view({"get": "list"})),
    path("jobs/run", JobViewSet.as_view({"post": "run"})),
    path("jobs/<int:pk>/output", JobViewSet.as_view({"get": "output"})),

    # 快捷命令执行
    path("quick/check", QuickCheckView.as_view()),
    path("quick/run", QuickRunView.as_view()),
    path("quick/history", HistoryView.as_view()),
    path("quick/batches/<int:pk>", BatchDetailView.as_view()),
    path("quick/batches/<int:pk>/cancel", BatchCancelView.as_view()),
    path("quick/jobs/<int:pk>/cancel", JobCancelView.as_view()),
    path("quick/jobs/<int:pk>/output", JobOutputView.as_view()),
]
