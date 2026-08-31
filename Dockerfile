# Python 3.12 or newer, because numpy 2.5 publishes no wheel below it. This
# image was on 3.10, where `pip install -r requirements.txt` cannot resolve at
# all — so it had not built since those versions were pinned. 3.13 is what the
# whole requirements set has Linux wheels for.
FROM python:3.13-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1

# chromium is a real dependency, not a convenience. The Art Director's second
# pass screenshots the finished page and critiques what it sees; with no
# browser present that stage disables itself silently and every site ships
# unreviewed. Installed headless-shell only — no X, no fonts beyond the
# defaults the pages already load from Google Fonts.
ENV CHROME_BIN=/usr/bin/chromium
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
      build-essential \
      curl \
      ca-certificates \
      chromium \
      fonts-liberation && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /root/.cache

COPY requirements.txt .
RUN pip install --upgrade pip wheel setuptools && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Everything the service writes lives here. Declared as a volume so a redeploy
# cannot take a seller's orders with it — see docker-compose.yml, which binds
# it to a path on the host.
# 755, not 777. The container runs as root, so the extra bits bought nothing
# and made a directory of customer orders world-writable on the host that binds
# it.
#
# Running as a non-root user would be better still, and is deliberately NOT
# done here: docker-compose binds ./data/local_store from the host, and an
# unprivileged uid inside the container would not own that directory. The
# service would then start cleanly and fail to write a single order. Doing it
# properly means fixing ownership on the host too, which belongs in the deploy,
# not silently in this file.
RUN mkdir -p /app/local_store /app/static && \
    chmod -R 755 /app/local_store /app/static
VOLUME ["/app/local_store"]

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
  CMD curl -fsS http://localhost:5000/health || exit 1

# api.server, not app:app. app.py is the older text-to-website entry point and
# carries none of the photo pipeline, commerce, publishing or agent — deploying
# it would serve a different product from the one that was tested.
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1"]
