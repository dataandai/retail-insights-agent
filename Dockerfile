FROM python:3.11-slim

WORKDIR /app

# Build dependencies for native packages (pyarrow, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source
COPY src/ ./src/
COPY config/ ./config/
COPY data/ ./data/
COPY evaluation/ ./evaluation/
COPY scripts/ ./scripts/

# Ensure local_data and logs directories exist
RUN mkdir -p /app/local_data /app/logs

# Default: interactive CLI
CMD ["python", "-m", "src.cli", "--user-id", "demo_manager", "--auth-token", "demo-token"]
