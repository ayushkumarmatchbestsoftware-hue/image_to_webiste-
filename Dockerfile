FROM python:3.10-slim-bookworm
WORKDIR /app
ENV ENVIRONMENT=production \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
      build-essential \
      libpq-dev \
      curl \
      ca-certificates \
      gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g vercel && \
    apt-get purge -y --auto-remove gnupg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /root/.cache /root/.npm
COPY requirements.txt .
RUN pip install --upgrade pip wheel setuptools && \
    pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1"]
