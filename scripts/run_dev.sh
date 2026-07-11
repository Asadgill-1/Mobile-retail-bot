#!/usr/bin/env bash
# Dev runner: starts FastAPI with reload on :8000.
# Telegram runs in long-polling mode (ADR-002); WhatsApp is mocked (ADR-002).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-}:$(pwd)/src"
uvicorn app.main:app --reload --port 8000
