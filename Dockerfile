# Dockerfile for Debian x86
# Use an official Python runtime based on Debian 12 (Bookworm)
# Platform: linux/amd64 (x86_64)
FROM --platform=linux/amd64 python:3.11-slim-bookworm

# Set the working directory in the container
WORKDIR /app

# Install system dependencies for psycopg2 and other packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file
COPY requirements.txt /app/requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy the entire backend application code into the container
COPY . /app

# Expose the port the app runs on
EXPOSE 8002

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]