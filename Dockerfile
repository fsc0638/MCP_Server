FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies if required by any python package
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose port 8000 for FastAPI (internal Docker port)
EXPOSE 8000

# Start Uvicorn
CMD ["uvicorn", "router:app", "--host", "0.0.0.0", "--port", "8000"]
