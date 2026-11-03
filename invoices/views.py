
import hmac
import json
import logging

import mailtrap as mt
from django.conf import settings
from django.core import signing
from django.http import (
    Http404,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import render
from django.utils.html import escape

from .services.email import EmailMessage, get_mailer
from django.views.decorators.csrf import csrf_exempt

from .models import Invoice
from .services.signed_links import get_invoice_from_token
from .services.webhooks import process_events

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "Mailtrap-Signature"


@csrf_exempt
def mailtrap_webhook(request, token: str):
    expected = settings.MAILTRAP_WEBHOOK_TOKEN
    if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
        raise Http404()
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    signing_secret = settings.MAILTRAP_WEBHOOK_SIGNING_SECRET
    if signing_secret and not mt.verify_signature(
        request.body, request.headers.get(SIGNATURE_HEADER, ""), signing_secret
    ):
        logger.warning("Rejected Mailtrap webhook with an invalid signature.")
        return JsonResponse({"error": "invalid signature"}, status=401)

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "malformed JSON"}, status=400)

    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return JsonResponse({"error": "expected an object with an 'events' list"}, status=400)

    result = process_events(events)
    return JsonResponse(
        {
            "ok": True,
            "received": result.received,
            "processed": result.processed,
            "duplicates": result.duplicates,
        }
    )


def client_invoice(request, token: str):
    try:
        invoice = get_invoice_from_token(token)
    except (signing.BadSignature, signing.SignatureExpired):
        return render(request, "public/invalid_link.html", status=400)
    except Invoice.DoesNotExist:
        return render(request, "public/invalid_link.html", status=404)

    return render(
        request,
        "public/invoice_detail.html",
        {
            "invoice": invoice,
            "token": token,
            "payment_url": invoice.payment_url or settings.PAYMENT_URL,
        },
    )


def dispute_invoice(request, token: str):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        invoice = get_invoice_from_token(token)
    except signing.BadSignature:
        return render(request, "public/invalid_link.html", status=400)
    except Invoice.DoesNotExist:
        return render(request, "public/invalid_link.html", status=404)

    # Do not process disputes for paid or void invoices.
    if invoice.status != Invoice.Status.SENT:
        return render(
            request,
            "public/invoice_detail.html",
            {
                "invoice": invoice,
                "token": token,
                "message": "This invoice cannot be disputed in its current status.",
            },
        )

    # Avoid sending duplicate notifications.
    if invoice.is_disputed:
        return render(
            request,
            "public/invoice_detail.html",
            {
                "invoice": invoice,
                "token": token,
                "dispute_submitted": True,
            },
        )

    invoice.is_disputed = True
    invoice.save(update_fields=["is_disputed"])

    if settings.OWNER_EMAIL:
        try:
            mailer = get_mailer()
            mailer.send(
                EmailMessage(
                    to_email=settings.OWNER_EMAIL,
                    subject=f"Invoice {invoice.number} disputed",
                    text=(
                        f"Invoice {invoice.number} has been disputed "
                        f"by {invoice.client.name}."
                    ),
                    html=(
                        f"<p>Invoice <strong>{escape(invoice.number)}</strong> "
                        f"has been disputed by "
                        f"{escape(invoice.client.name)}.</p>"
                    ),
                    category="invoice-dispute",
                )
            )
        except Exception:
            logging.exception(
                "Could not send dispute notification for invoice %s",
                invoice.pk,
            )

    return render(
        request,
        "public/invoice_detail.html",
        {
            "invoice": invoice,
            "token": token,
            "dispute_submitted": True,
        },
    )
