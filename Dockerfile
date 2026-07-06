FROM python:3.12-slim

# System dependencies:
#   tesseract-ocr / tesseract-ocr-eng  → OCR for PDF image extraction (cross-platform)
#   libmagic1                           → file type detection for unstructured
#   libgl1 / libglib2.0-0              → OpenCV transitive dep (renamed in Debian 13)
#   poppler-utils                       → pdfinfo / pdftotext used by pdfminer
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libmagic1 \
    libgl1 \
    libglib2.0-0 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps before copying code so this layer caches
# unless requirements.txt changes — keeps rebuilds fast.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Pre-download HuggingFace models at build time so startup is instant.
# These models are ~180MB total and are baked into the image layer.
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
from sentence_transformers.cross_encoder import CrossEncoder; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# Ports: 8000=FastAPI  8080=Chainlit  8501=Streamlit
# MCP server (8100) runs in its own container from sdlc-mcp-server/
EXPOSE 8000 8080 8501

CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
