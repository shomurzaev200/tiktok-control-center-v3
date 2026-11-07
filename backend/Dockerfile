FROM mcr.microsoft.com/playwright/python:v1.47.0-noble

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl postgresql-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY entrypoint.sh /entrypoint.sh
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app -s /sbin/nologin appuser \
    && mkdir -p /app/profiles /app/backups \
    && chown -R appuser:appgroup /app \
    && chmod 755 /entrypoint.sh

USER root
ENTRYPOINT ["/entrypoint.sh"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
