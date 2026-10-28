FROM python:3.12.12-slim-bookworm

ARG CHROME_VERSION=156.0.8078.47

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xvfb fonts-liberation \
    && curl -fsSL -o /tmp/chrome.deb "https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_${CHROME_VERSION}-1_amd64.deb" \
    && apt-get install -y /tmp/chrome.deb \
    && rm /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 avtobot \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin avtobot

WORKDIR /app
COPY bot.py browser.py card.py plate.py store.py /app/
RUN mkdir -p /app/secrets && chown -R 1000:1000 /app

USER 1000:1000
CMD ["python3", "-u", "/app/bot.py"]
