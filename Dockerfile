# Python 3.12 or newer, because numpy 2.5 publishes no wheel below it. This
# image was on 3.10, where `pip install -r requirements.txt` cannot resolve at
# all — so it had not built since those versions were pinned. 3.13 is what the
# whole requirements set has Linux wheels for.
FROM python:3.13-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
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
# Runs as an unprivileged user. Root in a container is one escape away from
# root on the host, and nothing here needs it.
#
# The uid is FIXED at 10001 and that matters more than it looks: docker-compose
# binds ./data/local_store from the host, and the host directory has to be
# writable by this uid or the service starts cleanly and cannot save a single
# order. On the deploy host, once:
#
#     sudo mkdir -p <app>/data/local_store
#     sudo chown -R 10001:10001 <app>/data/local_store
#
# core/storage.py checks the directory is writable at import and refuses to
# start if it is not, so getting this wrong is a loud failure at boot rather
# than a silent one at the seller's first order.
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin app && \
    mkdir -p /app/local_store /app/static && \
    chown -R 10001:10001 /app && \
    chmod -R 755 /app/local_store /app/static
VOLUME ["/app/local_store"]
USER 10001

# The port is read from the environment, because the deployment sets one.
# staging.yml passes PORT=8890 and docker-compose maps 5000; a hardcoded port
# meant the container listened somewhere the health check was not looking, and
# a deploy that cannot answer /health is rolled back as a failure.
ENV PORT=5000
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

# api.server, not app:app. app.py is the older text-to-website entry point and
# carries none of the photo pipeline, commerce, publishing or agent — deploying
# it would serve a different product from the one that was tested.
# Shell form so ${PORT} expands, and `exec` so uvicorn becomes PID 1 and gets
# the stop signal directly — without it the container ignores SIGTERM and every
# deploy waits out the full kill timeout.
#
# uvicorn, not `python -m api.server`: that module defines `app` and has no
# __main__ block, so running it as a script imports the application and exits
# immediately. The container would start, stop, and report nothing useful.
CMD ["sh", "-c", "exec uvicorn api.server:app --host 0.0.0.0 --port ${PORT} --workers 1"]
