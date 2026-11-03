
import datetime
from unittest.mock import patch

import pytest
from django.core import signing

from invoices.services.signed_links import (
    SALT,
    generate_invoice_token,
    get_invoice_from_token,
)


def test_valid_token_returns_invoice(make_invoice):
    invoice = make_invoice(due_date=datetime.date(2026, 3, 10))

    token = generate_invoice_token(invoice.pk)

    result = get_invoice_from_token(token)

    assert result == invoice


def test_tampered_token_is_rejected(make_invoice):
    invoice = make_invoice(due_date=datetime.date(2026, 3, 10))

    token = generate_invoice_token(invoice.pk)
    tampered_token = token + "tampered"

    with pytest.raises(signing.BadSignature):
        get_invoice_from_token(tampered_token)


def test_expired_token_is_rejected(make_invoice):
    invoice = make_invoice(due_date=datetime.date(2026, 3, 10))

    with patch("django.core.signing.time.time", return_value=1):
        expired_token = generate_invoice_token(invoice.pk)

    with pytest.raises(signing.SignatureExpired):
        get_invoice_from_token(expired_token)
