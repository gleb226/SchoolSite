FROM python:3.11-slim

WORKDIR /opt/render/project/src

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose the port
EXPOSE $PORT

# Command to run the application
CMD gunicorn --workers 2 --threads 2 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app
