# Bear backend image (PLAN Task 11). Build: docker build -t bear .
# Base images are pinned by digest; Dependabot proposes digest updates.

FROM python:3.13-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY backend/requirements.lock /tmp/requirements.lock
RUN /opt/venv/bin/pip install --require-hashes -r /tmp/requirements.lock

FROM python:3.13-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BEAR_MIGRATE_ON_BOOT=0 \
    FORWARDED_ALLOW_IPS=127.0.0.1 \
    DATABASE_URL=sqlite:////data/bear.db
RUN groupadd --system --gid 10001 bear \
    && useradd --system --uid 10001 --gid bear --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin bear \
    && mkdir /data && chown bear:bear /data
COPY --from=build /opt/venv /opt/venv
COPY backend/ /app/backend/
COPY shops/ /app/shops/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
WORKDIR /app/backend
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"]
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "--factory", "main:create_app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--no-access-log"]
