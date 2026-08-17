FROM python:3.10-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .

RUN python -m pip install --no-cache-dir --upgrade \
        pip==26.1 \
        "setuptools>=83.0.0" \
        wheel && \
    python -m pip install --no-cache-dir -r requirements.txt


FROM python:3.10-slim-bookworm AS runtime

ENV ENVIRONMENT=production \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
        libpq5 \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g vercel \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/* /root/.cache /root/.npm \
    && groupadd --gid 10001 appgroup \
    && useradd \
        --uid 10001 \
        --gid 10001 \
        --create-home \
        --home-dir /home/appuser \
        --shell /usr/sbin/nologin \
        appuser

COPY --from=builder /opt/venv /opt/venv

COPY --chown=appuser:appgroup . /app

RUN chmod -R u=rwX,g=rX,o=rX /app

USER 10001:10001

EXPOSE 5000

STOPSIGNAL SIGTERM

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1", "--no-server-header"]
