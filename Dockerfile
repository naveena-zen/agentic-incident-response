# ── Use official Python runtime as base ──────────────────────────
FROM python:3.10-slim

# Install system dependencies (build-essential needed for psutil/psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all source files
COPY . .

# Expose FastAPI and React default ports
EXPOSE 8000

# Set environment defaults
ENV PYTHONUNBUFFERED=1
ENV DB_HOST=db
ENV DB_PORT=5432
ENV DB_NAME=vigil
ENV DB_USER=postgres
ENV DB_PASSWORD=postgres

# Command to run FastAPI server
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
