FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/media /app/staticfiles /app/data \
    && chown -R app:app /app/media /app/staticfiles /app/data \
    && chmod +x /app/scripts/start.sh

USER app
EXPOSE 8000

# migrate + collectstatic, then Gunicorn with 3 workers
CMD ["/app/scripts/start.sh"]
