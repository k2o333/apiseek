#!/usr/bin/env bash
# Install New-API collector units and enable per-site once-timers.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SVC_SRC="$ROOT/newapi-monitor-once@.service"
TMR_SRC="$ROOT/newapi-monitor-once@.timer"
SVC_DST="/etc/systemd/system/newapi-monitor-once@.service"
TMR_DST="/etc/systemd/system/newapi-monitor-once@.timer"

usage() {
  cat <<'EOF'
Usage: install_newapi_service.sh [site_id ...]

Default sites: botcf torchai

Stop a site:
  systemctl disable --now newapi-monitor-once@<id>.timer
  systemctl stop newapi-monitor-once@<id>.service 2>/dev/null || true
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "missing .venv python" >&2
  exit 1
fi
if [[ ! -f "$SVC_SRC" || ! -f "$TMR_SRC" ]]; then
  echo "missing unit sources" >&2
  exit 1
fi

install -m 644 "$SVC_SRC" "$SVC_DST"
install -m 644 "$TMR_SRC" "$TMR_DST"
systemd-analyze verify "$SVC_DST" "$TMR_DST"

if ! grep -qE '^AccuracySec=1s' "$TMR_DST"; then
  echo "timer must set AccuracySec=1s" >&2
  exit 1
fi
if grep -qiE '^Requires=' "$TMR_DST"; then
  echo "timer must not use Requires=" >&2
  exit 1
fi
if grep -qE '^\[Install\]' "$SVC_DST"; then
  echo "service must not have [Install]" >&2
  exit 1
fi

systemctl daemon-reload

SITES=("$@")
if [[ ${#SITES[@]} -eq 0 ]]; then
  SITES=(botcf torchai)
fi

# Global site_id uniqueness across all sites/*.env
declare -A SEEN=()
for envf in "$ROOT"/sites/*.env; do
  [[ -f "$envf" ]] || continue
  base=$(basename "$envf" .env)
  sid=$(grep -E '^[[:space:]]*MONITOR_SITE_ID=' "$envf" | tail -n1 | cut -d= -f2- | tr -d '[:space:]"'"'" || true)
  sid=${sid:-$base}
  if [[ -n "${SEEN[$sid]:-}" ]]; then
    echo "error: duplicate MONITOR_SITE_ID=$sid in ${SEEN[$sid]} and $envf" >&2
    exit 1
  fi
  SEEN[$sid]=$envf
  if [[ "$base" != "$sid" ]]; then
    echo "error: env stem $base != MONITOR_SITE_ID $sid ($envf)" >&2
    exit 1
  fi
done

if [[ -d "$ROOT/sites" ]]; then
  chmod 700 "$ROOT/sites" 2>/dev/null || true
fi

FAILED=0
for site in "${SITES[@]}"; do
  env_file="$ROOT/sites/${site}.env"
  if [[ ! -f "$env_file" ]]; then
    echo "skip $site: missing $env_file" >&2
    FAILED=1
    continue
  fi
  chmod 600 "$env_file" || true
  if ! "$ROOT/.venv/bin/python" "$ROOT/newapi_monitor.py" --env-file "$env_file" --validate; then
    echo "skip $site: validate failed" >&2
    FAILED=1
    continue
  fi
  systemctl enable --now "newapi-monitor-once@${site}.timer"
  systemctl --no-pager --full status "newapi-monitor-once@${site}.timer" || true
  cat <<EOF
enabled newapi-monitor-once@${site}.timer
  logs: journalctl -u newapi-monitor-once@${site} -n 40 --no-pager
  stop:
    systemctl disable --now newapi-monitor-once@${site}.timer
    systemctl stop newapi-monitor-once@${site}.service 2>/dev/null || true
EOF
done

if [[ "$FAILED" -ne 0 ]]; then
  echo "one or more sites failed" >&2
  exit 1
fi
echo "done. list: systemctl list-timers 'newapi-monitor-once@*'"
