"""Run reminders immediately, then hourly, in a single foreground process."""

import logging
import os
import signal
import time
from threading import Event

import django
from django.core.management import call_command
from django.core.management.base import CommandError

logger = logging.getLogger("invoices.scheduler")


def run_scheduler(stop: Event, interval: float = 3600) -> None:
    while not stop.is_set():
        started = time.monotonic()
        try:
            call_command("send_payment_reminders")
        except CommandError:
            # A failed delivery must not disable all subsequent scheduled runs.
            logger.exception("Reminder run failed; retrying next hour")
        stop.wait(max(0, interval - (time.monotonic() - started)))


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    run_scheduler(stop)


if __name__ == "__main__":
    main()
