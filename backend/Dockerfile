FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    postgresql-client \
    ffmpeg \
    clamav \
    libjpeg-dev \
    libpng-dev \
    fonts-dejavu-core \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install playwright --break-system-packages
RUN playwright install chromium --with-deps

COPY . .

RUN chmod +x /app/scripts/*.sh

EXPOSE 8000

CMD ["uvicorn", "BACKEND_API_ROUTES:app", "--host", "0.0.0.0", "--port", "8000"]
