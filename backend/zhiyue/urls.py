"""执钥平台 URL 路由。"""
from django.urls import include, path
from django.http import JsonResponse


def health(_request):
    return JsonResponse({"status": "ok", "service": "zhiyue"})


urlpatterns = [
    path("api/health/", health),
    path("api/", include("hosts.urls")),
    path("api/", include("ops.urls")),
]
