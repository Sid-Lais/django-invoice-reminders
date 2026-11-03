
from django.conf import settings
from django.core import signing
from django.urls import reverse

SALT = "invoices.client-links"
MAX_AGE = 7 * 24 * 60 * 60  # 7 days


def generate_invoice_token(invoice_id: int) -> str:
    """Generate a signed, expiring token for an invoice."""
    return signing.dumps(
        {"invoice_id": invoice_id},
        salt=SALT,
    )


def get_invoice_from_token(token: str):
    """Validate the token and return its invoice ID."""
    from invoices.models import Invoice

    data = signing.loads(
        token,
        salt=SALT,
        max_age=MAX_AGE,
    )
    return Invoice.objects.get(pk=data["invoice_id"])


def invoice_url(invoice_id: int) -> str:
    """Build an absolute URL for a client's invoice."""
    token = generate_invoice_token(invoice_id)
    path = reverse("client-invoice", kwargs={"token": token})
    return f"{settings.SITE_URL}{path}"
