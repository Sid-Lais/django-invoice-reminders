FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY config ./config
COPY invoices ./invoices
COPY templates ./templates
COPY manage.py .
# Build static assets with public dummy values, never deployment credentials.
RUN DJANGO_SECRET_KEY=build-only-not-a-deployment-secret \
    MAILTRAP_API_TOKEN=build-only MAIL_FROM_EMAIL=build@example.com \
    MAILTRAP_WEBHOOK_TOKEN=build-only \
    python manage.py collectstatic --noinput

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --no-create-home app \
    && mkdir /data && chown app:app /data
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app /app
COPY docker ./docker
USER app
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --access-logfile -"]
