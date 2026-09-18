# Production Multi-Stage Dockerfile for GridWise Energy Optimizer
# Base image: Official slim Python
FROM python:3.12-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final runtime stage
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Create non-root system user for secure runtime execution
RUN groupadd -r appgroup && useradd -r -g appgroup -s /sbin/nologin appuser

# Copy application source code
COPY app/ ./app/
COPY README.md .

RUN chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

# Bind to 0.0.0.0 and dynamic PORT env var
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
