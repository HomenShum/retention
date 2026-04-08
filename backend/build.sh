#!/usr/bin/env bash
# Render build script — runs during deploy
# Installs Python deps + Playwright Chromium browser

set -o errexit

pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright Chromium browser + system dependencies
playwright install --with-deps chromium
