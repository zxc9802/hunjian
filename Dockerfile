# Repository-root entrypoint for Zeabur Git deployments.
FROM python:3.12-slim-bookworm
WORKDIR /app
COPY workbench/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && useradd --uid 10001 --create-home workbench \
    && mkdir /data && chown workbench:workbench /data
COPY --chown=workbench:workbench workbench /app/workbench
ENV PYTHONUNBUFFERED=1 WORKBENCH_DATA=/data VOICE_FILE=/app/workbench/reference/speaker-reference.mp3
USER workbench
EXPOSE 8788
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request,os; from urllib.parse import urlsplit; r=urllib.request.Request('http://127.0.0.1:8788/health',headers={'Host':urlsplit(os.environ['PUBLIC_URL']).netloc}); urllib.request.urlopen(r,timeout=3)"
CMD ["python", "-m", "workbench", "--host", "0.0.0.0", "--port", "8788"]
