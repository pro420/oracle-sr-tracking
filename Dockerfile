FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SRT_DB=/data/srs.db \
    PORT=8000

WORKDIR /app
COPY pyproject.toml README.md ./
COPY srtrack ./srtrack
RUN pip install --no-cache-dir '.[web]' \
    && useradd --create-home --uid 1000 srt \
    && mkdir -p /data && chown srt:srt /data

USER srt
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz', timeout=4)"

# One worker keeps SQLite writes simple; threads handle concurrent users.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --access-logfile - srtrack.wsgi:app"]
