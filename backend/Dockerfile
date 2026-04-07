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
# Install CPU-only PyTorch first so sentence-transformers never pulls the 2 GB CUDA build
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the local embedding model so the container works offline
# and the first request doesn't stall on a ~400 MB download.
# Override at build time with: docker build --build-arg EMBED_MODEL=other/model
ARG EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBED_MODEL}')"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "BACKEND_API_ROUTES:app", "--host", "0.0.0.0", "--port", "8000"]
