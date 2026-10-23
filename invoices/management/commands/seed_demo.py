import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from invoices.models import Client, Invoice, LineItem, default_time_zone, validate_time_zone

DEMO_CLIENT_NAME = "Demo Client"

# (invoice number, days until due relative to today, currency, line items)
DEMO_INVOICES = [
    ("DEMO-0001", 3, "USD", [("Website maintenance – October", "1", "1200.00")]),
    ("DEMO-0002", 0, "EUR", [("Logo design", "1", "1800.00"), ("Brand guidelines PDF", "1", "450.00")]),
    ("DEMO-0003", -7, "USD", [("Consulting (hours)", "6", "95.00")]),
    ("DEMO-0004", -14, "EUR", [("Annual hosting – Café Zürich ☕", "1", "640.00")]),
]


class Command(BaseCommand):
    help = (
        "Create a demo client and 4 sent invoices (due in 3 days, due today, 7 and 14 days "
        "overdue) so one reminder run sends each stage once. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            default="demo-client@example.com",
            help="Email address for the demo client (use your own inbox to receive the emails).",
        )
        parser.add_argument(
            "--time-zone",
            help="IANA time zone for the demo client, e.g. America/New_York (default: TIME_ZONE).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing demo invoices (and their reminder history) first, then recreate.",
        )

    @transaction.atomic
    def handle(self, *args, email, reset=False, time_zone=None, **options):
        if time_zone:
            try:
                validate_time_zone(time_zone)
            except ValidationError as exc:
                raise CommandError(exc.messages[0]) from exc
        client, created = Client.objects.get_or_create(
            email__iexact=email,
            defaults={
                "name": DEMO_CLIENT_NAME,
                "company": "Northwind GmbH",
                "email": email,
                "time_zone": time_zone or default_time_zone(),
            },
        )
        if time_zone and client.time_zone != time_zone:
            client.time_zone = time_zone
            client.save(update_fields=["time_zone"])
        self.stdout.write(
            f"{'Created' if created else 'Using existing'} client: {client.name} <{client.email}> "
            f"in {client.time_zone}"
        )
        today = client.local_date()  # "due today" means today where the client is

        if reset:
            deleted, _ = Invoice.objects.filter(number__in=[n for n, *_ in DEMO_INVOICES]).delete()
            self.stdout.write(f"Deleted {deleted} demo object(s).")

        tz = client.tzinfo
        for number, days_until_due, currency, items in DEMO_INVOICES:
            if Invoice.objects.filter(number=number).exists():
                self.stdout.write(f"  {number} already exists – skipped")
                continue
            due = today + datetime.timedelta(days=days_until_due)
            issue = due - datetime.timedelta(days=30)
            invoice = Invoice.objects.create(
                number=number,
                client=client,
                issue_date=issue,
                due_date=due,
                currency=currency,
                tax_rate=Decimal("0"),
                status=Invoice.Status.SENT,
                sent_at=datetime.datetime.combine(issue, datetime.time(10, 0), tzinfo=tz),
                notes="Demo invoice created by seed_demo.",
            )
            for description, quantity, unit_price in items:
                LineItem.objects.create(
                    invoice=invoice,
                    description=description,
                    quantity=Decimal(quantity),
                    unit_price=Decimal(unit_price),
                )
            when = (
                f"due in {days_until_due} days"
                if days_until_due > 0
                else "due today" if days_until_due == 0 else f"{-days_until_due} days overdue"
            )
            self.stdout.write(self.style.SUCCESS(f"  {number} created – {when} ({invoice.total_display})"))

        if email.endswith("@example.com"):
            self.stdout.write(
                self.style.WARNING(
                    "Note: the demo client uses an example.com address. "
                    "Re-run with --email you@yourdomain.com --reset to receive real emails."
                )
            )
