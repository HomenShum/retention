FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies needed by Python packages AND Playwright Chromium.
# Must be in ONE layer before playwright install-deps, because slim images
# have no cached apt lists after build.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    # Playwright Chromium runtime deps (libnss3, libatk, libcups, etc.)
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libdbus-1-3 libxkbcommon0 libatspi2.0-0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libwayland-client0 \
    # Fonts for rendering
    fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser binary
RUN playwright install chromium

# Copy application code
COPY . .

# Default port — overridden by $PORT on Render, Railway, Fly.io
ENV PORT=8000

EXPOSE ${PORT}

# Health check against /api/health
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:' + os.environ.get('PORT','8000') + '/api/health')" || exit 1

# Start uvicorn — reads $PORT at runtime
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
