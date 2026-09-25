FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Create directory for SQLite DB
RUN mkdir -p /data

# Expose port (Render sets $PORT at runtime)
EXPOSE 10000

# Entrypoint — wsgi.py initialises the DB on first run
CMD gunicorn --workers 2 --threads 2 --timeout 120 --bind 0.0.0.0:${PORT:-10000} wsgi:app
