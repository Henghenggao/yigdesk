FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt package.json package-lock.json ./
RUN pip install --no-cache-dir -r requirements.txt \
    && npm ci --omit=dev
COPY . .

EXPOSE 8080
CMD ["python", "-m", "yigdesk.app"]
