FROM python:3.11-slim

WORKDIR /opt/render/project/src

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY . .

# Expose the application port
EXPOSE 10000

# Command to run the application with 1 worker and multiple threads for SQLite + Bot safety
CMD gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app
