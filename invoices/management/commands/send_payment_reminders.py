import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from invoices.services.reminders import run_reminders


def parse_date(value: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"--date must be YYYY-MM-DD, got {value!r}") from exc


class Command(BaseCommand):
    help = "Send due payment reminders for sent invoices. Safe to run repeatedly."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be sent without sending or writing anything.",
        )
        parser.add_argument(
            "--date",
            type=parse_date,
            help=(
                "Override 'today' (YYYY-MM-DD) for every client, ignoring the send window. "
                "Without it, each client's own local date and working hours are used."
            ),
        )

    def handle(self, *args, dry_run=False, date=None, **options):
        summary = run_reminders(today=date, dry_run=dry_run)

        if summary.today is not None:
            heading = f"Payment reminders for {summary.today.isoformat()} (all clients, send window ignored)"
        else:
            start, end = settings.REMINDER_SEND_HOURS
            heading = (
                f"Payment reminders at {timezone.localtime(summary.now):%Y-%m-%d %H:%M %Z} – "
                f"each client's local date, sending {start:02d}:00–{end:02d}:00 their time"
            )
        if dry_run:
            heading += " (DRY RUN – nothing sent, nothing written)"
        self.stdout.write(self.style.MIGRATE_HEADING(heading))

        for outcome in summary.outcomes:
            line = outcome.describe()
            if outcome.action == "failed":
                self.stdout.write(self.style.ERROR(line))
            elif outcome.action in ("sent", "would-send"):
                self.stdout.write(self.style.SUCCESS(line))
            elif outcome.action in ("flagged", "waiting"):
                self.stdout.write(self.style.WARNING(line))
            elif outcome.skipped or outcome.detail:
                self.stdout.write(line)

        sent_label = "would send" if dry_run else "sent"
        sent_count = sum(o.action == "would-send" for o in summary.outcomes) if dry_run else summary.sent
        self.stdout.write(
            f"Summary: checked={summary.checked} {sent_label}={sent_count} "
            f"skipped={summary.skipped} waiting={summary.waiting} failed={summary.failed}"
        )
        if summary.failed:
            raise CommandError(f"{summary.failed} reminder(s) failed; they will be retried on the next run.")
