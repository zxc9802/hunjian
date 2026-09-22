FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    PYTHONPATH=/app/scripts:/app/deploy OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
RUN sed -i 's|http://deb.debian.org/debian$|https://mirrors.tuna.tsinghua.edu.cn/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 install -y --no-install-recommends ffmpeg fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/requirements.txt
COPY deploy/requirements.txt /app/deploy/requirements.txt
RUN pip install --no-cache-dir -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple -r /app/deploy/requirements.txt
COPY scripts /app/scripts
COPY assets /app/assets
COPY deploy/*.py /app/deploy/
RUN chmod -R a+rX /app
USER 1026:10
EXPOSE 8780
CMD ["python", "/app/deploy/start.py"]
