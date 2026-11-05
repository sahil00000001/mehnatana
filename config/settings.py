"""
Settings for Mehnatana.

Everything that differs between computers (passwords, hosts, the database)
is read from the environment / the .env file. See .env.example.
"""
import os
import warnings
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name, default=""):
    return os.environ.get(name, default).strip()


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


# ------------------------------------------------------------ product name
# To rename the product change APP_NAME here (or in .env). It is not written anywhere else.
APP_NAME = env("APP_NAME", "Mehnatana")
APP_TAGLINE = env("APP_TAGLINE", "Har mehnat ka pakka hisaab.")
SUPPORT_PHONE = env("SUPPORT_PHONE", "+91 98765 43210")
SUPPORT_EMAIL = env("SUPPORT_EMAIL", "")

# ------------------------------------------------------------ core
DEBUG = env_bool("DEBUG", False)
SECRET_KEY = env("SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set SECRET_KEY in the environment (.env) when DEBUG is off.")
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "core",
    "accounts",
    "companies",
    "statutory",
    "employees",
    "advances",
    "payroll",
    "excel_io",
    "reports",
    "letters",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.TenantMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.app",
            ],
        },
    },
]

# ------------------------------------------------------------ database
# No database settings -> SQLite file (local development).
# DATABASE_URL or POSTGRES_HOST -> PostgreSQL (Supabase or any other server).
# All tables live in their own schema (POSTGRES_SCHEMA, default "mehnatana") and
# every Mehnatana table name starts with "mtn_", so nothing mixes with other tables.
DB_SCHEMA = env("POSTGRES_SCHEMA", "mehnatana")


def _postgres_settings():
    url = env("DATABASE_URL")
    if url:
        parts = urlparse(url)
        query = parse_qs(parts.query)
        return {
            "NAME": unquote(parts.path.lstrip("/")) or "postgres",
            "USER": unquote(parts.username or ""),
            "PASSWORD": unquote(parts.password or ""),
            "HOST": parts.hostname or "",
            "PORT": str(parts.port or 5432),
            "SSLMODE": query.get("sslmode", [env("POSTGRES_SSLMODE", "prefer")])[0],
        }
    if env("POSTGRES_HOST"):
        return {
            "NAME": env("POSTGRES_DB", "postgres"),
            "USER": env("POSTGRES_USER", "postgres"),
            "PASSWORD": env("POSTGRES_PASSWORD"),
            "HOST": env("POSTGRES_HOST"),
            "PORT": env("POSTGRES_PORT", "5432"),
            "SSLMODE": env("POSTGRES_SSLMODE", "prefer"),
        }
    return None


_pg = _postgres_settings()
if _pg:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _pg["NAME"],
            "USER": _pg["USER"],
            "PASSWORD": _pg["PASSWORD"],
            "HOST": _pg["HOST"],
            "PORT": _pg["PORT"],
            "OPTIONS": {"sslmode": _pg["SSLMODE"]},
            # Seconds a connection is kept open. 0 on Vercel: each request opens and closes its own.
            "CONN_MAX_AGE": int(env("CONN_MAX_AGE", "60")),
            "CONN_HEALTH_CHECKS": True,
            # Safe with connection poolers such as Supabase's.
            "DISABLE_SERVER_SIDE_CURSORS": True,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": Path(env("SQLITE_PATH", str(BASE_DIR / "db.sqlite3"))),
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ------------------------------------------------------------ login
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.LockoutBackend"]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_MINUTES = 15

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Session ends after 8 hours without activity.
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_SAVE_EVERY_REQUEST = True

# ------------------------------------------------------------ language and time
LANGUAGE_CODE = env("LANGUAGE_CODE", "en-in")
LANGUAGES = [("en-in", "English")]
TIME_ZONE = env("TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [BASE_DIR / "locale"]

# ------------------------------------------------------------ files
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
# FILE_STORAGE=database keeps uploaded files (logos, sheets) in the database instead of the
# media folder — needed on Vercel, whose disk is not kept between requests.
FILE_STORAGE = env("FILE_STORAGE", "filesystem")
STORAGES = {
    "default": {"BACKEND": "core.storage.DatabaseStorage" if FILE_STORAGE == "database"
                else "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
# On Vercel there is no collectstatic step: WhiteNoise serves the files straight from the app folders.
WHITENOISE_USE_FINDERS = env_bool("WHITENOISE_USE_FINDERS", False)

# Uploaded sheets and logos. This folder is private: there is no public URL for it;
# files are returned only through views that check the company (core/views.py).
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))

# Before the first collectstatic there is no staticfiles folder; WhiteNoise only needs to say so once.
warnings.filterwarnings("ignore", message=r"No directory at: .*staticfiles")

# README: uploads up to 5 MB. Vercel accepts requests up to 4.5 MB, so it is set to 4 there.
MAX_UPLOAD_BYTES = int(env("MAX_UPLOAD_MB", "5")) * 1024 * 1024
MAX_LOGO_BYTES = 500 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# ------------------------------------------------------------ email (optional)
EMAIL_HOST = env("EMAIL_HOST")
EMAIL_ENABLED = bool(EMAIL_HOST)
if EMAIL_ENABLED:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = int(env("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = env("EMAIL_HOST_USER")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
    DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# ------------------------------------------------------------ security
X_FRAME_OPTIONS = "DENY"
CSRF_FAILURE_VIEW = "core.views.csrf_failure"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# Caddy redirects every http:// request to https://, so Django does not need to (W008).
# HSTS preload is left for the domain owner to decide (W021).
SILENCED_SYSTEM_CHECKS = ["security.W008", "security.W021"]
# Behind Caddy the visitor's address is in X-Forwarded-For (used by the audit log).
TRUST_X_FORWARDED_FOR = env_bool("TRUST_X_FORWARDED_FOR", not DEBUG)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}
