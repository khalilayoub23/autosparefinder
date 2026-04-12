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
# Install everything in one pass; --extra-index-url ensures torch resolves
# to the CPU-only wheel (no nvidia-cudnn/cublas/nccl packages, ~700 MB not 2 GB)
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.txt

# Pre-download the local embedding model so the container works offline
# and the first request doesn't stall on a ~400 MB download.
# Override at build time with: docker build --build-arg EMBED_MODEL=other/model
ARG EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBED_MODEL}')"

COPY . .

RUN chmod +x /app/start.sh

EXPOSE 8000

ENV DISCOVERY_TARGET=120 \
    DISCOVERY_PER_RUN=20 \
    DISCOVERY_TRUCK_DELAY_DAYS=30 \
    DEPLOYED_AT_ISO=2026-04-02T00:00:00Z

CMD ["/app/start.sh"]
