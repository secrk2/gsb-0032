"""作业 / 仪表盘路由。"""
from django.urls import path

from .views import DashboardView, JobViewSet

urlpatterns = [
    path("dashboard", DashboardView.as_view()),
    path("jobs", JobViewSet.as_view({"get": "list"})),
    path("jobs/run", JobViewSet.as_view({"post": "run"})),
    path("jobs/<int:pk>/output", JobViewSet.as_view({"get": "output"})),
]
