FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data /tmp && chown -R 10001:10001 /data /tmp

WORKDIR /app

COPY requirements-bot.txt requirements-worker.txt ./
RUN pip install -r requirements-bot.txt -r requirements-worker.txt

COPY bot ./bot
COPY worker ./worker
COPY docker/entrypoint-single.sh ./entrypoint.sh

RUN chmod +x ./entrypoint.sh && chown -R 10001:10001 /app

USER 10001:10001

EXPOSE 8080 10000

CMD ["./entrypoint.sh"]
