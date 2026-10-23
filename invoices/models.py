import re
import zoneinfo
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.utils import timezone

TWO_PLACES = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def default_time_zone() -> str:
    return settings.TIME_ZONE


def validate_time_zone(value: str) -> None:
    try:
        zoneinfo.ZoneInfo(value)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, TypeError) as err:
        raise ValidationError(
            f"Unknown time zone {value!r}. Use an IANA name such as America/New_York or Europe/Berlin."
        ) from err


class Client(models.Model):
    class EmailStatus(models.TextChoices):
        OK = "ok", "OK"
        BOUNCED = "bounced", "Bounced"
        SPAM = "spam", "Spam complaint"

    name = models.CharField(max_length=200)
    company = models.CharField(max_length=200, blank=True)
    email = models.EmailField()
    email_status = models.CharField(
        max_length=10, choices=EmailStatus.choices, default=EmailStatus.OK
    )
    email_flagged_at = models.DateTimeField(null=True, blank=True)
    email_flag_reason = models.TextField(blank=True)
    time_zone = models.CharField(
        max_length=64,
        default=default_time_zone,
        validators=[validate_time_zone],
        help_text="The client's time zone, e.g. America/New_York. Reminders follow their local date and hours.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.company})" if self.company else self.name

    @property
    def is_email_flagged(self) -> bool:
        return self.email_status != self.EmailStatus.OK

    @property
    def tzinfo(self) -> zoneinfo.ZoneInfo:
        try:
            return zoneinfo.ZoneInfo(self.time_zone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError, TypeError):
            return timezone.get_default_timezone()

    def local_now(self, now=None):
        """`now` (default: current time) in the client's time zone."""
        return timezone.localtime(now or timezone.now(), self.tzinfo)

    def local_date(self, now=None):
        return self.local_now(now).date()

    def clear_email_flag(self) -> None:
        self.email_status = self.EmailStatus.OK
        self.email_flagged_at = None
        self.email_flag_reason = ""
        self.save(update_fields=["email_status", "email_flagged_at", "email_flag_reason"])


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    class Currency(models.TextChoices):
        USD = "USD", "USD – US Dollar"
        EUR = "EUR", "EUR – Euro"
        GBP = "GBP", "GBP – British Pound"
        AUD = "AUD", "AUD – Australian Dollar"
        CAD = "CAD", "CAD – Canadian Dollar"
        SGD = "SGD", "SGD – Singapore Dollar"
        AED = "AED", "AED – UAE Dirham"
        INR = "INR", "INR – Indian Rupee"

    number = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        help_text="Leave blank to auto-generate (e.g. INV-2026-0001).",
    )
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="invoices")
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField()
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.USD)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)
    tax_rate = models.DecimalField(
        "tax rate (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issue_date", "-id"]

    def __str__(self) -> str:
        return self.number or f"Invoice #{self.pk}"

    def clean(self):
        if self.issue_date and self.due_date and self.due_date < self.issue_date:
            raise ValidationError({"due_date": "Due date cannot be before the issue date."})

    def save(self, *args, **kwargs):
        if self.number:
            return super().save(*args, **kwargs)
        # Auto-number; retry if a concurrent save grabbed the same number.
        for _ in range(5):
            self.number = self._next_number()
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                if not Invoice.objects.filter(number=self.number).exists():
                    self.number = ""
                    raise  # not a numbering collision
                self.number = ""
        raise IntegrityError("Could not allocate a unique invoice number.")

    def _next_number(self) -> str:
        year = (self.issue_date or timezone.localdate()).year
        prefix = f"INV-{year}-"
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        highest = 0
        for number in Invoice.objects.filter(number__startswith=prefix).values_list(
            "number", flat=True
        ):
            match = pattern.match(number)
            if match:
                highest = max(highest, int(match.group(1)))
        return f"{prefix}{highest + 1:04d}"

    @property
    def pdf_filename(self) -> str:
        return f"Invoice-{self.number}.pdf"

    @property
    def subtotal(self) -> Decimal:
        return money(sum((item.amount for item in self.line_items.all()), Decimal("0")))

    @property
    def tax_amount(self) -> Decimal:
        return money(self.subtotal * Decimal(self.tax_rate) / Decimal("100"))

    @property
    def total(self) -> Decimal:
        return money(self.subtotal + self.tax_amount)

    def format_money(self, amount: Decimal) -> str:
        return f"{self.currency} {amount:,.2f}"

    @property
    def total_display(self) -> str:
        return self.format_money(self.total)

    def days_overdue_on(self, today) -> int:
        if self.status != self.Status.SENT or not self.due_date:
            return 0
        return max(0, (today - self.due_date).days)

    @property
    def days_overdue(self) -> int:
        return self.days_overdue_on(self.client.local_date())


class LineItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="line_items")
    description = models.CharField(max_length=500)
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return self.description

    @property
    def amount(self) -> Decimal:
        if self.quantity is None or self.unit_price is None:
            return Decimal("0.00")
        return money(Decimal(self.quantity) * Decimal(self.unit_price))


class ReminderLog(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="reminder_logs")
    stage = models.CharField(max_length=50)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    scheduled_for = models.DateField()
    sent_at = models.DateTimeField(null=True, blank=True)
    message_id = models.CharField(max_length=255, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scheduled_for", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "stage"], name="unique_reminder_stage_per_invoice"
            )
        ]

    def __str__(self) -> str:
        return f"{self.invoice} · {self.stage} · {self.status}"


class WebhookEvent(models.Model):
    event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=50)
    email = models.EmailField(blank=True)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"{self.event_type} · {self.email}"
