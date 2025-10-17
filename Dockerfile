FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    VIBE_RESIZER_SECRET=change-me \
    UPLOAD_DIR=/app/uploads \
    OUTPUT_DIR=/app/outputs

RUN mkdir -p /app/uploads /app/outputs

CMD ["gunicorn", "app:app", "-b", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "--timeout", "600"]
