from django.conf import settings


def business(request):
    return {"business_name": settings.BUSINESS_NAME}
