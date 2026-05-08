FROM python:3.12-slim AS base

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    pandoc \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY version.py .
COPY epub_to_md_converter.py .
COPY html_to_md_converter.py .
COPY pdf_to_md_converter.py .
COPY medium_scraper.py .
COPY gui.py .
COPY templates/ templates/

# Create default output directories
RUN mkdir -p /app/output /app/input

# Expose the GUI port (3763 = "EPMD" on a phone keypad)
EXPOSE 3763

# Default: run the web GUI
CMD ["python", "gui.py"]
