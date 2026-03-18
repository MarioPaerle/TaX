#!/bin/bash
# SSH Manager — one-shot setup + run
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

echo ""
echo "  ⬡ SSH Manager setup"
echo "  ─────────────────────────────"

# Create venv if missing
if [ ! -d "$VENV" ]; then
    echo "  → Creating virtual environment..."
    python3 -m venv "$VENV"
fi

# Activate
source "$VENV/bin/activate"

# Install deps
echo "  → Installing dependencies..."
pip install --quiet flask flask-sock paramiko groq

echo "  → Done. Starting app..."
echo ""

python "$SCRIPT_DIR/app2.py"