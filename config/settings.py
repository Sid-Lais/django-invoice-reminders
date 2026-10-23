"""Django settings for django-invoice-reminders.

All secrets and per-deployment values come from environment variables.
A local `.env` file is loaded if present (real env vars always win).
"""

from pathlib import Path

from .env import env_bool, env_hour_range, env_list, env_str, load_dotenv, require_env

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

DEBUG = env_bool("DJANGO_DEBUG", False)

require_env(
    ["MAILTRAP_API_TOKEN", "MAIL_FROM_EMAIL", "MAILTRAP_WEBHOOK_TOKEN"]
    + ([] if DEBUG else ["DJANGO_SECRET_KEY"])
)

SECRET_KEY = env_str("DJANGO_SECRET_KEY") or "django-insecure-dev-only-key-do-not-use-in-production"
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
SITE_URL = env_str("SITE_URL", "http://localhost:8000").rstrip("/")
CSRF_TRUSTED_ORIGINS = [SITE_URL] if SITE_URL.startswith("https://") else []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "invoices",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

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
                "invoices.context.business",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite by default. For scheduled runs on ephemeral machines (e.g. GitHub
# Actions) the database must live somewhere persistent - see README.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": env_str("SQLITE_PATH") or BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env_str("TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Email (Mailtrap Email API, transactional stream) -----------------------
MAILER_CLASS = env_str("MAILER_CLASS", "invoices.services.email.MailtrapMailer")
MAILTRAP_API_TOKEN = env_str("MAILTRAP_API_TOKEN")
MAIL_FROM_EMAIL = env_str("MAIL_FROM_EMAIL")
MAIL_FROM_NAME = env_str("MAIL_FROM_NAME") or env_str("BUSINESS_NAME", "Invoices")
MAILTRAP_WEBHOOK_TOKEN = env_str("MAILTRAP_WEBHOOK_TOKEN")
MAILTRAP_WEBHOOK_SIGNING_SECRET = env_str("MAILTRAP_WEBHOOK_SIGNING_SECRET")

# --- Business details printed on PDFs and emails -----------------------------
# Only the business name is printed: no postal address, email address or
# payment/bank details appear in emails or PDFs, by design.
BUSINESS_NAME = env_str("BUSINESS_NAME", "Invoices")

# --- Reminder sequence --------------------------------------------------------
# Each client has their own time zone (Client.time_zone, default TIME_ZONE).
# Stage dates use the client's local date, and reminders are only sent while
# the client's local time is inside this window, e.g. "9-18" = 09:00-17:59.
# Run send_payment_reminders every hour so every time zone hits the window.
REMINDER_SEND_HOURS = env_hour_range("REMINDER_SEND_HOURS", "9-18")

# offset_days is relative to the invoice due_date. `subject` is a str.format()
# template; available keys: number, due_date, total, business_name, days_overdue.
# Templates live in templates/<template>.txt and templates/<template>.html.
REMINDER_STAGES = [
    {
        "key": "heads_up",
        "offset_days": -3,
        "category": "reminder-heads-up",
        "subject": "Coming up: invoice {number} is due on {due_date}",
        "template": "emails/reminder_heads_up",
    },
    {
        "key": "due_today",
        "offset_days": 0,
        "category": "reminder-due-today",
        "subject": "Invoice {number} is due today",
        "template": "emails/reminder_due_today",
    },
    {
        "key": "overdue",
        "offset_days": 7,
        "category": "reminder-overdue",
        "subject": "Invoice {number} is now {days_overdue} days overdue",
        "template": "emails/reminder_overdue",
    },
    {
        "key": "final_notice",
        "offset_days": 14,
        "category": "reminder-final",
        "subject": "Final notice: invoice {number} ({total}) is overdue",
        "template": "emails/reminder_final_notice",
    },
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "loggers": {"invoices": {"handlers": ["console"], "level": "INFO"}},
}
