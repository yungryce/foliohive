# syntax=docker/dockerfile:1.6

########################################
# Base CPU image
########################################
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

########################################
# GPU image (optional target)
########################################
FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04 AS gpu-base

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential python3-pip python3.13 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt && \
    python3 -m pip install --no-cache-dir torch==2.2.2+cu118 torchvision==0.17.2+cu118 -f https://download.pytorch.org/whl/torch_stable.html

########################################
# Final image
########################################
FROM base AS app

WORKDIR /app
COPY models/ ./models/
COPY train_worker.py .

ENV TRAINING_MODE=serverless

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

CMD ["python", "train_worker.py"]
