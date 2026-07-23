#!/usr/bin/env bash
# Install New-API collector units and enable per-site groups timers.
# Models daily templates are installed but never enabled unless explicitly requested.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SVC_SRC="$ROOT/newapi-monitor-once@.service"
TMR_SRC="$ROOT/newapi-monitor-once@.timer"
SVC_DST="/etc/systemd/system/newapi-monitor-once@.service"
TMR_DST="/etc/systemd/system/newapi-monitor-once@.timer"
MODELS_SVC_SRC="$ROOT/newapi-models-daily@.service"
MODELS_TMR_SRC="$ROOT/newapi-models-daily@.timer"
MODELS_SVC_DST="/etc/systemd/system/newapi-models-daily@.service"
MODELS_TMR_DST="/etc/systemd/system/newapi-models-daily@.timer"

usage() {
  cat <<'EOF'
Usage: install_newapi_service.sh [--enable-models] [site_id ...]

Default sites: botcf torchai

Models daily templates are installed by default but are not enabled. After a
successful models bootstrap, opt in explicitly:
  install_newapi_service.sh --enable-models <id>

Stop a site:
  systemctl disable --now newapi-monitor-once@<id>.timer
  systemctl stop newapi-monitor-once@<id>.service 2>/dev/null || true
EOF
}

ENABLE_MODELS=0
SITES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --enable-models)
      ENABLE_MODELS=1
      shift
      ;;
    --)
      shift
      SITES+=("$@")
      break
      ;;
    -*)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      SITES+=("$1")
      shift
      ;;
  esac
done

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "missing .venv python" >&2
  exit 1
fi
if [[ ! -f "$SVC_SRC" || ! -f "$TMR_SRC" || ! -f "$MODELS_SVC_SRC" || ! -f "$MODELS_TMR_SRC" ]]; then
  echo "missing unit sources" >&2
  exit 1
fi

install -m 644 "$SVC_SRC" "$SVC_DST"
install -m 644 "$TMR_SRC" "$TMR_DST"
install -m 644 "$MODELS_SVC_SRC" "$MODELS_SVC_DST"
install -m 644 "$MODELS_TMR_SRC" "$MODELS_TMR_DST"
systemd-analyze verify "$SVC_DST" "$TMR_DST" "$MODELS_SVC_DST" "$MODELS_TMR_DST"

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
if ! grep -qE '^OnCalendar=\*-\*-\* 00:00:00 Asia/Shanghai$' "$MODELS_TMR_DST"; then
  echo "models timer must run at Shanghai midnight" >&2
  exit 1
fi
if ! grep -qE '^RandomizedDelaySec=300$' "$MODELS_TMR_DST"; then
  echo "models timer must set RandomizedDelaySec=300" >&2
  exit 1
fi
if ! grep -qE '^TimeoutStartSec=600$' "$MODELS_SVC_DST"; then
  echo "models service must set TimeoutStartSec=600" >&2
  exit 1
fi
if ! grep -q -- '--models-refresh' "$MODELS_SVC_DST"; then
  echo "models service must run --models-refresh" >&2
  exit 1
fi
if grep -qE '^\[Install\]' "$MODELS_SVC_DST"; then
  echo "models service must not have [Install]" >&2
  exit 1
fi

systemctl daemon-reload

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
  if [[ "$ENABLE_MODELS" -eq 1 ]]; then
    systemctl enable --now "newapi-models-daily@${site}.timer"
    systemctl --no-pager --full status "newapi-models-daily@${site}.timer" || true
  fi
  cat <<EOF
enabled newapi-monitor-once@${site}.timer
  logs: journalctl -u newapi-monitor-once@${site} -n 40 --no-pager
  stop:
    systemctl disable --now newapi-monitor-once@${site}.timer
    systemctl stop newapi-monitor-once@${site}.service 2>/dev/null || true
EOF
  if [[ "$ENABLE_MODELS" -eq 1 ]]; then
    echo "enabled newapi-models-daily@${site}.timer"
  else
    echo "installed newapi-models-daily@ templates; timer remains disabled"
  fi
done

if [[ "$FAILED" -ne 0 ]]; then
  echo "one or more sites failed" >&2
  exit 1
fi
echo "done. list: systemctl list-timers 'newapi-monitor-once@*'"
