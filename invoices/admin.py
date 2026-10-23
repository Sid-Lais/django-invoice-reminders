import zoneinfo

from django import forms
from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import Client, Invoice, LineItem, ReminderLog, WebhookEvent
from .services.email import EmailSendError
from .services.invoicing import (
    InvoiceStateError,
    mark_paid,
    resend_invoice,
    send_invoice,
    void_invoice,
)
from .services.pdf import render_invoice_pdf

BADGE_COLORS = {
    Client.EmailStatus.BOUNCED: "#dc2626",  # red
    Client.EmailStatus.SPAM: "#ea580c",  # orange
}


def _badge(text: str, color: str):
    return format_html(
        '<span style="background:{};color:#fff;padding:2px 8px;border-radius:10px;'
        'font-size:11px;font-weight:600;white-space:nowrap">{}</span>',
        color,
        text,
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


TIME_ZONE_CHOICES = [(name, name.replace("_", " ")) for name in sorted(zoneinfo.available_timezones())]


class ClientForm(forms.ModelForm):
    time_zone = forms.ChoiceField(
        choices=TIME_ZONE_CHOICES,
        help_text="The client's time zone. Reminders follow their local date and working hours.",
    )

    class Meta:
        model = Client
        fields = "__all__"


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    form = ClientForm
    list_display = (
        "name",
        "company",
        "email",
        "time_zone",
        "client_local_time",
        "email_status_badge",
        "email_flagged_at",
    )
    list_filter = ("email_status",)
    search_fields = ("name", "company", "email", "time_zone")

    @admin.display(description="Their local time")
    def client_local_time(self, obj):
        return f"{obj.local_now():%a %H:%M}"
    readonly_fields = ("email_flagged_at", "email_flag_reason", "created_at")
    actions = ["clear_email_flag"]

    @admin.display(description="Email status", ordering="email_status")
    def email_status_badge(self, obj):
        color = BADGE_COLORS.get(obj.email_status)
        if not color:
            return format_html('<span style="color:#16a34a">{}</span>', "OK")
        return _badge(obj.get_email_status_display().upper(), color)

    @admin.action(description="Clear email flag (allow emails again)")
    def clear_email_flag(self, request, queryset):
        count = 0
        for client in queryset.exclude(email_status=Client.EmailStatus.OK):
            client.clear_email_flag()
            count += 1
        self.message_user(request, f"Cleared the email flag on {_plural(count, 'client')}.")


class LineItemInline(admin.TabularInline):
    model = LineItem
    extra = 1
    fields = ("description", "quantity", "unit_price", "amount")
    readonly_fields = ("amount",)

    def get_extra(self, request, obj=None, **kwargs):
        return 0 if obj and obj.line_items.exists() else 3


class ReminderLogInline(admin.TabularInline):
    model = ReminderLog
    extra = 0
    can_delete = False
    fields = ("stage", "status", "scheduled_for", "sent_at", "message_id", "error")
    readonly_fields = fields
    verbose_name_plural = "Reminder history"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    inlines = [LineItemInline, ReminderLogInline]
    list_display = (
        "number",
        "client",
        "total_with_currency",
        "due_date",
        "status_badge",
        "days_overdue_display",
        "last_reminder",
        "pdf_link",
    )
    list_filter = ("status", ("due_date", admin.DateFieldListFilter), "currency")
    search_fields = ("number", "client__name", "client__company", "client__email")
    date_hierarchy = "due_date"
    autocomplete_fields = ("client",)
    readonly_fields = ("status", "sent_at", "paid_at", "created_at", "totals", "pdf_link")
    fieldsets = (
        (None, {"fields": ("number", "client", "status", ("issue_date", "due_date"))}),
        ("Amounts", {"fields": (("currency", "tax_rate"), "totals")}),
        ("Notes", {"fields": ("notes",)}),
        ("History", {"fields": (("sent_at", "paid_at"), "created_at", "pdf_link")}),
    )
    actions = ["send_invoice_action", "resend_invoice_action", "mark_paid_action", "void_action"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("client")
            .prefetch_related("line_items", "reminder_logs")
        )

    def get_urls(self):
        custom = [
            path(
                "<int:pk>/pdf/",
                self.admin_site.admin_view(self.pdf_view),
                name="invoices_invoice_pdf",
            )
        ]
        return custom + super().get_urls()

    def pdf_view(self, request, pk):
        invoice = get_object_or_404(
            Invoice.objects.select_related("client").prefetch_related("line_items"), pk=pk
        )
        if not self.has_view_permission(request, invoice):
            return HttpResponse(status=403)
        response = HttpResponse(render_invoice_pdf(invoice), content_type="application/pdf")
        disposition = "attachment" if request.GET.get("download") else "inline"
        response["Content-Disposition"] = f'{disposition}; filename="{invoice.pdf_filename}"'
        return response

    # --- columns ---------------------------------------------------------
    @admin.display(description="Total")
    def total_with_currency(self, obj):
        return obj.total_display

    @admin.display(description="Totals")
    def totals(self, obj):
        if not obj.pk:
            return "Save the invoice to see totals."
        return format_html(
            "Subtotal {}<br>Tax {}<br><strong>Total {}</strong>",
            obj.format_money(obj.subtotal),
            obj.format_money(obj.tax_amount),
            obj.total_display,
        )

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        colors = {"draft": "#6b7280", "sent": "#2563eb", "paid": "#16a34a", "void": "#9ca3af"}
        return _badge(obj.get_status_display(), colors.get(obj.status, "#6b7280"))

    @admin.display(description="Days overdue")
    def days_overdue_display(self, obj):
        days = obj.days_overdue
        if not days:
            return "–"
        return format_html('<strong style="color:#dc2626">{}</strong>', days)

    @admin.display(description="Last reminder")
    def last_reminder(self, obj):
        sent = [log for log in obj.reminder_logs.all() if log.status == ReminderLog.Status.SENT]
        if not sent:
            return "–"
        last = max(sent, key=lambda log: log.sent_at)
        return f"{last.stage} ({timezone.localtime(last.sent_at):%d %b %Y})"

    @admin.display(description="PDF")
    def pdf_link(self, obj):
        if not obj.pk:
            return "–"
        url = reverse("admin:invoices_invoice_pdf", args=[obj.pk])
        return format_html(
            '<a href="{}" target="_blank">Preview</a> · <a href="{}?download=1">Download</a>', url, url
        )

    # --- actions ---------------------------------------------------------
    def _run(self, request, queryset, func, *, allowed, verb):
        ok = failed = skipped = 0
        for invoice in queryset:
            if invoice.status not in allowed:
                skipped += 1
                continue
            try:
                func(invoice)
                ok += 1
            except InvoiceStateError as exc:
                failed += 1
                self.message_user(request, str(exc), messages.ERROR)
            except EmailSendError as exc:
                failed += 1
                self.message_user(request, f"{invoice.number}: email failed – {exc}", messages.ERROR)
        if ok:
            self.message_user(request, f"{verb} {_plural(ok, 'invoice')}.", messages.SUCCESS)
        if failed:
            self.message_user(request, f"{_plural(failed, 'invoice')} failed.", messages.ERROR)
        if skipped:
            self.message_user(
                request,
                f"Skipped {_plural(skipped, 'invoice')} (status must be {' or '.join(allowed)}).",
                messages.WARNING,
            )

    @admin.action(description="Send invoice (email PDF to client)")
    def send_invoice_action(self, request, queryset):
        self._run(request, queryset, send_invoice, allowed=["draft"], verb="Sent")

    @admin.action(description="Resend invoice (already sent)")
    def resend_invoice_action(self, request, queryset):
        self._run(request, queryset, resend_invoice, allowed=["sent"], verb="Re-sent")

    @admin.action(description="Mark as paid (send receipt, stop reminders)")
    def mark_paid_action(self, request, queryset):
        ok = receipt_failed = skipped = 0
        for invoice in queryset:
            if invoice.status != Invoice.Status.SENT:
                skipped += 1
                continue
            try:
                mark_paid(invoice)
                ok += 1
            except EmailSendError as exc:
                ok += 1
                receipt_failed += 1
                self.message_user(
                    request,
                    f"{invoice.number} is marked paid, but the receipt email failed – {exc}",
                    messages.WARNING,
                )
        if ok:
            self.message_user(
                request, f"Marked {_plural(ok, 'invoice')} as paid.", messages.SUCCESS
            )
        if receipt_failed:
            self.message_user(
                request, f"{_plural(receipt_failed, 'receipt')} failed to send.", messages.ERROR
            )
        if skipped:
            self.message_user(
                request, f"Skipped {_plural(skipped, 'invoice')} (only sent invoices can be paid).", messages.WARNING
            )

    @admin.action(description="Void invoice")
    def void_action(self, request, queryset):
        self._run(request, queryset, void_invoice, allowed=["draft", "sent"], verb="Voided")


@admin.register(ReminderLog)
class ReminderLogAdmin(admin.ModelAdmin):
    list_display = ("invoice", "stage", "status", "scheduled_for", "sent_at", "message_id")
    list_filter = ("status", "stage")
    search_fields = ("invoice__number", "invoice__client__email", "message_id")
    readonly_fields = [f.name for f in ReminderLog._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "email", "event_id", "received_at")
    list_filter = ("event_type",)
    search_fields = ("email", "event_id")
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]

    def has_add_permission(self, request):
        return False
