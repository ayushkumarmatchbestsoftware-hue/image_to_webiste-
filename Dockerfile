FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies (required for Postgres drivers, image processing, and Vercel CLI)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g vercel \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip wheel
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose FastAPI port
EXPOSE 5077

# Set environment variables
ENV ENVIRONMENT=production
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Default command: FastAPI with Uvicorn (not Gunicorn+Flask)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5077", "--workers", "4"]
