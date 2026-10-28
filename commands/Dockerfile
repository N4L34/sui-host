FROM python:3.12.12-slim-bookworm

RUN groupadd --gid 1000 commands \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin commands

WORKDIR /app
COPY app/scripts /app/scripts
RUN mkdir -p /app/secrets /app/deploy/rendered && chown -R 1000:1000 /app

USER 1000:1000
CMD ["python3", "-u", "/app/scripts/telegram_bot.py"]
