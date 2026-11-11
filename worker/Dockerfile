FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git libgl1 libgomp1 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY worker ./worker
# Pinned vendor dependency, installed inside the video worker image.
RUN mkdir -p /app/vendor && git clone https://github.com/kajisho5/ffmpeg-skill.git /app/vendor/ffmpeg-skill \
    && cd /app/vendor/ffmpeg-skill && git checkout 0317ef8c8f2ee300783844afc8f99ca421a0b508 \
    && rm -rf .git
ENV FFMPEG_SKILL_MCP_SERVER=/app/vendor/ffmpeg-skill/mcp/server.py
RUN mkdir -p /data/uploads /data/work /data/outputs

ENV PYTHONUNBUFFERED=1 MEDIA_ROOT=/data
EXPOSE 8000
CMD ["uvicorn", "worker.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

