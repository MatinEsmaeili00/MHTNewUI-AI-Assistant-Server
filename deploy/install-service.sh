#!/usr/bin/env bash
# Installs or updates the systemd USER unit for the folder this repository is in, and (re)starts it.
# No root needed. Run it again after moving the repository.
#
#   deploy/install-service.sh
#
# Keeping it running after logout and starting it at boot needs "linger" once (asks for the password):
#   sudo loginctl enable-linger "$USER"
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
unit_dir="$HOME/.config/systemd/user"

[ -x "$repo/.venv/bin/uvicorn" ] || { echo "No .venv in $repo: do step 2 of docs/SETUP_SPARK.md first."; exit 1; }
[ -f "$repo/.env" ] || echo "Warning: no .env in $repo, so no API key (docs/SETUP_SPARK.md, step 4)."

mkdir -p "$unit_dir"
# The unit in the repository is written for ~/MHTNewUI-AI-Assistant-Server; put the real folder in.
sed "s#%h/MHTNewUI-AI-Assistant-Server#$repo#g" "$repo/deploy/mht-assistant.service" > "$unit_dir/mht-assistant.service"

systemctl --user daemon-reload
systemctl --user enable mht-assistant
systemctl --user restart mht-assistant

port="$(grep -E '^PORT=' "$repo/.env" 2>/dev/null | tail -1 | cut -d= -f2)"
port="${port:-8010}"
for _ in $(seq 1 20); do
    if curl -s -m 2 "http://localhost:$port/health"; then
        echo
        break
    fi
    sleep 1
done
systemctl --user --no-pager --lines=0 status mht-assistant | head -4
loginctl show-user "$USER" 2>/dev/null | grep Linger || true
