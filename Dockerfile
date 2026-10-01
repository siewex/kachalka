FROM python:3.12-slim

# ffmpeg converts exercise GIFs to sharper, smaller MP4s on first start
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot

ENV DATA_DIR=/app/data PYTHONUNBUFFERED=1
VOLUME /app/data
CMD ["python", "-m", "bot"]
