#!/bin/bash
set -euo pipefail

echo "Pulling latest changes..."
git pull origin main

echo "Installing backend dependencies in virtualenv..."
cd backend || exit 1
if [ -d "venv" ]; then
  source venv/bin/activate
else
  python3 -m venv venv
  source venv/bin/activate
fi
pip install -r requirements.txt --upgrade-strategy only-if-needed

echo "Running migrations..."
alembic upgrade head

echo "Building frontend..."
cd ../frontend && npm ci --no-audit --no-fund && npm run build || true

echo "Restarting services (systemd)…"
sudo systemctl restart autospare-backend nginx || true

echo "Deployed!"
