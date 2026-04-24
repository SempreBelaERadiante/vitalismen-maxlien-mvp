#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/opt/maxlien-mvp"
BRANCH="${1:-main}"
SERVICE="maxlien-mvp"

cd "$REPO_DIR"

echo "[maxlien-mvp] Fetching origin/$BRANCH"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "[maxlien-mvp] Validating Python entrypoint"
python3 -m py_compile app.py wsgi.py

echo "[maxlien-mvp] Restarting systemd service"
systemctl restart "$SERVICE"
systemctl is-active "$SERVICE" >/dev/null

echo "[maxlien-mvp] Deploy finished"
