"""Hermetic settings for pytest: dummy credentials, FakeMailer, fixed time zone.

Values are assigned before the base settings import, so the .env loader
(which never overrides existing variables) cannot leak real secrets in.
"""

import os

os.environ.update(
    {
        "MAILTRAP_API_TOKEN": "test-token-not-real",
        "MAIL_FROM_EMAIL": "billing@example.com",
        "MAIL_FROM_NAME": "Invoices",
        "MAILTRAP_WEBHOOK_TOKEN": "test-webhook-token",
        "MAILTRAP_WEBHOOK_SIGNING_SECRET": "",
        "DJANGO_SECRET_KEY": "test-secret-key",
        "DJANGO_DEBUG": "false",
        "TIME_ZONE": "Asia/Kolkata",
        "SITE_URL": "http://testserver",
        "ALLOWED_HOSTS": "testserver,localhost",
        "BUSINESS_NAME": "Invoices",
        "MAILER_CLASS": "invoices.services.email.FakeMailer",
        # Whole day by default so tests don't depend on the clock; window tests override it.
        "REMINDER_SEND_HOURS": "0-24",
    }
)

from .settings import *  # noqa: E402,F401,F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
