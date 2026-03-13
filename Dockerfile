FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Make pip behavior deterministic in Docker builds
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

# Install system dependencies if required by any python package
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN python -m pip install --upgrade pip setuptools wheel \
    && for i in 1 2 3; do \
        pip install --no-cache-dir --index-url https://pypi.org/simple -r requirements.txt && break; \
        echo "pip install failed (attempt $i), retrying..."; \
        sleep 5; \
        if [ "$i" = "3" ]; then exit 1; fi; \
    done

# Copy the rest of the application
COPY . .

# Expose port 8000 for FastAPI (internal Docker port)
EXPOSE 8000

# Start Uvicorn
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
