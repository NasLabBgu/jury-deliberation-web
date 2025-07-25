# Use Python 3.11 slim image for better security and smaller size
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=production
ENV PORT=8080
ENV HOST=0.0.0.0

# Create app directory
WORKDIR /app

# Install system dependencies including build tools
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY flask-app/requirements-prod.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-prod.txt

# Copy application code
COPY flask-app/ .

# Create non-root user for security
RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app
USER appuser

# Create directories for uploads and ensure backend is accessible
RUN mkdir -p /app/uploads

# Expose port
EXPOSE 8080

# Health check with longer timeout
HEALTHCHECK --interval=30s --timeout=30s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run the application with Gunicorn using eventlet for WebSocket support
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--worker-class", "eventlet", "--workers", "1", "--timeout", "300", "--graceful-timeout", "300", "--keep-alive", "2", "--max-requests", "1000", "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info", "wsgi:application"]
