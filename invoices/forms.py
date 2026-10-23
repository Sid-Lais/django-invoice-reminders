from decimal import Decimal

from django import forms
from django.db import transaction
from django.utils import timezone

from .models import Client, Invoice, LineItem


class InvoiceForm(forms.Form):
    name = forms.CharField(label="Invoice name", max_length=500)
    amount = forms.DecimalField(
        label="Amount", max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )
    currency = forms.ChoiceField(
        choices=[(code, code) for code, _ in Invoice.Currency.choices], initial=Invoice.Currency.USD
    )
    due_date = forms.DateField(label="Due date", widget=forms.DateInput(attrs={"type": "date"}))
    client_email = forms.EmailField(label="Send to", max_length=254)
    client_name = forms.CharField(label="Client name", max_length=200, required=False)
    send_now = forms.BooleanField(label="Email the invoice to the client now", required=False, initial=True)

    def clean_due_date(self):
        due = self.cleaned_data["due_date"]
        if due < timezone.localdate():
            raise forms.ValidationError("The due date can't be in the past.")
        return due

    @transaction.atomic
    def save(self) -> Invoice:
        data = self.cleaned_data
        email = data["client_email"].strip().lower()
        client = Client.objects.filter(email__iexact=email).order_by("id").first()
        if client is None:
            fallback = email.split("@")[0].replace(".", " ").replace("_", " ").title()
            client = Client.objects.create(name=data["client_name"].strip() or fallback, email=email)
        invoice = Invoice.objects.create(
            client=client, due_date=data["due_date"], currency=data["currency"]
        )
        LineItem.objects.create(
            invoice=invoice, description=data["name"].strip(), quantity=Decimal("1"), unit_price=data["amount"]
        )
        return invoice
