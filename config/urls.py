from django.contrib import admin
from django.urls import path

from invoices import ui
from invoices.views import mailtrap_webhook

admin.site.site_header = "Invoice Reminders"
admin.site.site_title = "Invoice Reminders"
admin.site.index_title = "Invoices, clients and reminders"

urlpatterns = [
    path("", ui.dashboard, name="dashboard"),
    path("invoices/new/", ui.create_invoice, name="invoice-create"),
    path("invoices/<int:pk>/<str:action>/", ui.invoice_action, name="invoice-action"),
    path("admin/", admin.site.urls),
    path("webhooks/mailtrap/<str:token>/", mailtrap_webhook, name="mailtrap-webhook"),
]
