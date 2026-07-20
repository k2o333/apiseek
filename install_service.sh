#!/usr/bin/env bash
# Install Sub2API monitor systemd template and enable site instances.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
UNIT_SRC="$ROOT/sub2api-monitor@.service"
UNIT_DST="/etc/systemd/system/sub2api-monitor@.service"

if [[ ! -f "$UNIT_SRC" ]]; then
  echo "missing $UNIT_SRC" >&2
  exit 1
fi
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "project venv missing; create .venv and pip install -r requirements.txt" >&2
  exit 1
fi

install -m 644 "$UNIT_SRC" "$UNIT_DST"
systemd-analyze verify "$UNIT_DST"
systemctl daemon-reload

SITES=("$@")
if [[ ${#SITES[@]} -eq 0 ]]; then
  SITES=(aiapibank pinaic)
fi

for site in "${SITES[@]}"; do
  env_file="$ROOT/sites/${site}.env"
  if [[ ! -f "$env_file" ]]; then
    echo "skip $site: missing $env_file" >&2
    continue
  fi
  chmod 600 "$env_file" || true
  systemctl enable --now "sub2api-monitor@${site}.service"
  systemctl --no-pager --full status "sub2api-monitor@${site}.service" || true
done
