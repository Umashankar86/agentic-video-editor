FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libgomp1 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY worker ./worker
RUN mkdir -p /data/uploads /data/work /data/outputs

ENV PYTHONUNBUFFERED=1 MEDIA_ROOT=/data
EXPOSE 8000
CMD ["uvicorn", "worker.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

