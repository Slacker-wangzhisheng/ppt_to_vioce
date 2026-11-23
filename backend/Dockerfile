ARG PYTHON_BASE_IMAGE=python:3.12-slim
ARG FFMPEG_IMAGE=mwader/static-ffmpeg:7.1

FROM ${FFMPEG_IMAGE} AS ffmpeg
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 视频渲染依赖 ffmpeg / ffprobe（静态二进制，避免 apt 网络问题）
COPY --from=ffmpeg /ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg /ffprobe /usr/local/bin/ffprobe

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
