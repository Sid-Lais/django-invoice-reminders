import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from invoices.models import Client, Invoice, LineItem
from invoices.services.email import FakeMailer


@pytest.fixture(autouse=True)
def outbox():
    """Fresh FakeMailer state for every test; yields the captured payload list."""
    FakeMailer.reset()
    yield FakeMailer.outbox
    FakeMailer.reset()


@pytest.fixture
def client_obj(db):
    return Client.objects.create(name="Ananya Rao", company="Rao & Co", email="ananya@example.com")


def local_dt(day: datetime.date, hour: int = 10) -> datetime.datetime:
    return datetime.datetime.combine(day, datetime.time(hour), tzinfo=timezone.get_current_timezone())


@pytest.fixture
def make_invoice(db, client_obj):
    def factory(
        *,
        due_date: datetime.date,
        sent_on: datetime.date | None = None,
        status: str = Invoice.Status.SENT,
        client: Client | None = None,
        items=(("Design work", "2", "1500.00"),),
        tax_rate: str = "18",
        currency: str = "USD",
    ) -> Invoice:
        if sent_on is None and status != Invoice.Status.DRAFT:
            sent_on = due_date - datetime.timedelta(days=30)
        invoice = Invoice.objects.create(
            client=client or client_obj,
            issue_date=sent_on or due_date - datetime.timedelta(days=30),
            due_date=due_date,
            status=status,
            sent_at=local_dt(sent_on) if sent_on else None,
            tax_rate=Decimal(tax_rate),
            currency=currency,
        )
        for description, quantity, price in items:
            LineItem.objects.create(
                invoice=invoice, description=description, quantity=Decimal(quantity), unit_price=Decimal(price)
            )
        return invoice

    return factory


def categories(outbox) -> list[str]:
    return [payload["category"] for payload in outbox]
