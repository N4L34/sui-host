FROM ghcr.io/xtls/xray-core:26.10.21 AS xray
FROM python:3.12.12-slim-bookworm
COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
WORKDIR /app
COPY __init__.py service.py /app/egress/
USER 1000:1000
ENTRYPOINT ["python3", "-m", "egress.service"]
