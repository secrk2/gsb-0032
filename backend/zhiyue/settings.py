"""执钥运维平台 - Django 配置。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).lower() in ("1", "true", "yes", "on")


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-key-change-in-production")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "django_filters",
    "hosts",
    "ops",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "zhiyue.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "zhiyue.wsgi.application"
ASGI_APPLICATION = "zhiyue.asgi.application"

# 容器里用 MySQL；本地冒烟测试（无 MYSQL_HOST）时退回 sqlite
if env("MYSQL_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": env("MYSQL_DATABASE", "zhiyue"),
            "USER": env("MYSQL_USER", "zhiyue"),
            "PASSWORD": env("MYSQL_PASSWORD", "zhiyue"),
            "HOST": env("MYSQL_HOST", "mysql"),
            "PORT": env("MYSQL_PORT", "3306"),
            "OPTIONS": {"charset": "utf8mb4"},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
}

CORS_ALLOW_ALL_ORIGINS = True

# 实时推送 / 作业输出缓冲
REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")
REDIS_EVENTS_CHANNEL = "zhiyue:events"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
    if env("REDIS_URL")
    else {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}

# 凭据加密密钥（Fernet key）。生产环境务必通过环境变量注入。
CREDENTIAL_KEY = env(
    "CREDENTIAL_KEY", "4SyJchvLbp5gPm_uAdA9MplOj_ORGxIJy_cEysoSBzY="
)

# 在线探测开关 / 间隔（秒）
ONLINE_CHECK_ENABLED = env_bool("ONLINE_CHECK_ENABLED", True)
ONLINE_CHECK_INTERVAL = int(env("ONLINE_CHECK_INTERVAL", "30"))
ONLINE_CHECK_TIMEOUT = float(env("ONLINE_CHECK_TIMEOUT", "4"))
