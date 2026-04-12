FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    postgresql-client \
    ffmpeg \
    clamav \
    libjpeg-dev \
    libpng-dev \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.txt

COPY . .

RUN chmod +x /app/start.sh

EXPOSE 8000

ENV DISCOVERY_TARGET=120 \
    DISCOVERY_PER_RUN=20 \
    DISCOVERY_TRUCK_DELAY_DAYS=30 \
    DEPLOYED_AT_ISO=2026-04-02T00:00:00Z

CMD ["/app/start.sh"]
