#!/usr/bin/env bash
# Starts the MHT assistant server (port from PORT, default 8010, reachable from other machines).
# Settings: environment variables, or a .env file next to this script (see .env.example).
cd "$(dirname "$0")"
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi
source .venv/bin/activate
exec uvicorn server:app --host 0.0.0.0 --port "${PORT:-8010}"
