# 1. Name the stage "requirements-stage"
FROM python:3.11-slim-bookworm AS requirements-stage

WORKDIR /tmp

# 2. Install Poetry AND the export plugin
RUN pip install poetry poetry-plugin-export

COPY pyproject.toml poetry.lock ./

# 3. Export requirements to /tmp/requirements.txt
RUN poetry export -f requirements.txt --output requirements.txt --without-hashes

# --- Final Stage ---
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies: Tesseract OCR + Chromium for Selenium
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Tesseract OCR
    tesseract-ocr \
    libtesseract-dev \
    tesseract-ocr-ind \
    # Chromium and ChromeDriver for Selenium
    chromium \
    chromium-driver \
    # Required dependencies for headless Chrome
    fonts-liberation \
    libnss3 \
    libxss1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*

# Set Chrome environment variables for Selenium
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# 4. Copy from the named stage
COPY --from=requirements-stage /tmp/requirements.txt /app/requirements.txt

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy application code
COPY . /app

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]