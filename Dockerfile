FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (required for Postgres drivers, image processing, and Vercel CLI)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .

# Install explicitly defined dependencies
RUN pip install --upgrade pip==25.3 wheel==0.46.2
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose port (as seen in app.py main block)
EXPOSE 5000

# Set environment variables
ENV ENVIRONMENT=production
ENV PYTHONUNBUFFERED=1

# Default command for the web service (Industry Standard Gunicorn)
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
