"""ASGI 入口（uvicorn 承载同步 + SSE 异步视图）。"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zhiyue.settings")
application = get_asgi_application()
