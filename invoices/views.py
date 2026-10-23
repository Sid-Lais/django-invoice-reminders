import hmac
import json
import logging

import mailtrap as mt
from django.conf import settings
from django.http import Http404, HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

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
