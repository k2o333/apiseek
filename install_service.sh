#!/usr/bin/env bash
# Install Sub2API monitor units and enable per-site once-timers (default production path).
# Old simple units (sub2api-monitor@) are kept on disk for rollback; not enabled by default.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
ONCE_SERVICE_SRC="$ROOT/sub2api-monitor-once@.service"
ONCE_TIMER_SRC="$ROOT/sub2api-monitor-once@.timer"
ONCE_SERVICE_DST="/etc/systemd/system/sub2api-monitor-once@.service"
ONCE_TIMER_DST="/etc/systemd/system/sub2api-monitor-once@.timer"
OLD_SERVICE_SRC="$ROOT/sub2api-monitor@.service"
OLD_SERVICE_DST="/etc/systemd/system/sub2api-monitor@.service"
MODELS_SERVICE_SRC="$ROOT/sub2api-models-daily@.service"
MODELS_TIMER_SRC="$ROOT/sub2api-models-daily@.timer"
MODELS_SERVICE_DST="/etc/systemd/system/sub2api-models-daily@.service"
MODELS_TIMER_DST="/etc/systemd/system/sub2api-models-daily@.timer"

# Must match sub2api-monitor-once@.timer defaults (architecture §3.3 / §3.4).
TIMER_ON_INACTIVE_SEC=240
TIMER_RANDOMIZED_DELAY_SEC=60
# Expected midpoint: OnUnitInactiveSec + RandomizedDelaySec/2
TIMER_EXPECTED_MIDPOINT=$((TIMER_ON_INACTIVE_SEC + TIMER_RANDOMIZED_DELAY_SEC / 2))
INTERVAL_TOLERANCE_SEC=30

usage() {
  cat <<'EOF'
Usage: install_service.sh [options] [site_id ...]

Install once service/timer templates and enable sub2api-monitor-once@<site>.timer
for each site (default: aiapibank pinaic).

Options:
  --legacy-simple     Enable old Type=simple sub2api-monitor@ units instead of once-timers
  --install-models    Install models daily unit templates only (does NOT enable timers)
  --enable-models     After install, enable models daily timers for listed sites
                      (requires prior --models-bootstrap per site; not default)
  -h, --help          Show this help

Environment checks per site (once path):
  - sites/<id>.env exists, mode tightened to 0600
  - POLL_INTERVAL_SECONDS within 30s of timer expected midpoint (~270s)
  - Old simple unit not active (refuse dual-run; stop it first or use --legacy-simple)

Models daily (opt-in):
  Templates are installed when sources exist; timers are NEVER auto-enabled on
  unbootstrapped sites. Enable only after:
    .venv/bin/python sub2api_monitor.py --env-file sites/<id>.env --models-preflight
    .venv/bin/python sub2api_monitor.py --env-file sites/<id>.env --models-bootstrap
  then: $0 --enable-models <id>

Rollback one site to simple:
  systemctl disable --now sub2api-monitor-once@<id>.timer
  systemctl stop sub2api-monitor-once@<id>.service 2>/dev/null || true
  systemctl enable --now sub2api-monitor@<id>.service
EOF
}

LEGACY_SIMPLE=0
INSTALL_MODELS=0
ENABLE_MODELS=0
SITES=()
for arg in "$@"; do
  case "$arg" in
    -h|--help)
      usage
      exit 0
      ;;
    --legacy-simple)
      LEGACY_SIMPLE=1
      ;;
    --install-models)
      INSTALL_MODELS=1
      ;;
    --enable-models)
      ENABLE_MODELS=1
      INSTALL_MODELS=1
      ;;
    -*)
      echo "unknown option: $arg" >&2
      usage >&2
      exit 2
      ;;
    *)
      SITES+=("$arg")
      ;;
  esac
done

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "project venv missing; create .venv and pip install -r requirements.txt" >&2
  exit 1
fi

if [[ ! -f "$ONCE_SERVICE_SRC" || ! -f "$ONCE_TIMER_SRC" ]]; then
  echo "missing once unit sources under $ROOT" >&2
  exit 1
fi
if [[ ! -f "$OLD_SERVICE_SRC" ]]; then
  echo "missing $OLD_SERVICE_SRC" >&2
  exit 1
fi

# Keep old simple template installed for rollback; install new once units.
install -m 644 "$OLD_SERVICE_SRC" "$OLD_SERVICE_DST"
install -m 644 "$ONCE_SERVICE_SRC" "$ONCE_SERVICE_DST"
install -m 644 "$ONCE_TIMER_SRC" "$ONCE_TIMER_DST"

# Models daily templates: install when present; never auto-enable on sites.
VERIFY_UNITS=("$ONCE_SERVICE_DST" "$ONCE_TIMER_DST" "$OLD_SERVICE_DST")
if [[ -f "$MODELS_SERVICE_SRC" && -f "$MODELS_TIMER_SRC" ]]; then
  install -m 644 "$MODELS_SERVICE_SRC" "$MODELS_SERVICE_DST"
  install -m 644 "$MODELS_TIMER_SRC" "$MODELS_TIMER_DST"
  VERIFY_UNITS+=("$MODELS_SERVICE_DST" "$MODELS_TIMER_DST")
  if ! grep -qE 'OnCalendar=\*-\*-\* 00:00:00 Asia/Shanghai' "$MODELS_TIMER_DST"; then
    echo "models timer must use OnCalendar Asia/Shanghai 00:00" >&2
    exit 1
  fi
  if ! grep -qE '^RandomizedDelaySec=300' "$MODELS_TIMER_DST"; then
    echo "models timer must set RandomizedDelaySec=300" >&2
    exit 1
  fi
  if ! grep -qE '^TimeoutStartSec=600' "$MODELS_SERVICE_DST"; then
    echo "models service must set TimeoutStartSec=600" >&2
    exit 1
  fi
  if ! grep -q -- '--models-refresh' "$MODELS_SERVICE_DST"; then
    echo "models service must ExecStart --models-refresh" >&2
    exit 1
  fi
  if grep -qE '^\[Install\]' "$MODELS_SERVICE_DST"; then
    echo "models service must not have [Install] (enable timer only)" >&2
    exit 1
  fi
  echo "installed models daily unit templates (timers not enabled by default)"
elif [[ $INSTALL_MODELS -eq 1 || $ENABLE_MODELS -eq 1 ]]; then
  echo "missing models unit sources under $ROOT" >&2
  exit 1
fi

if ! systemd-analyze verify "${VERIFY_UNITS[@]}"; then
  echo "systemd-analyze verify failed" >&2
  exit 1
fi

# Static checks from architecture §9.2
if ! grep -qE '^AccuracySec=1s[[:space:]]*$' "$ONCE_TIMER_DST"; then
  echo "timer must set AccuracySec=1s" >&2
  exit 1
fi
if grep -qiE '^Requires=' "$ONCE_TIMER_DST"; then
  echo "timer must not use Requires= (boot stampede)" >&2
  exit 1
fi
if ! grep -qE '^TimeoutStartSec=240[[:space:]]*$' "$ONCE_SERVICE_DST"; then
  echo "once service must set TimeoutStartSec=240" >&2
  exit 1
fi
if grep -qE '^\[Install\]' "$ONCE_SERVICE_DST"; then
  echo "once service must not have [Install] section (enable timer only)" >&2
  exit 1
fi

systemctl daemon-reload

if [[ ${#SITES[@]} -eq 0 ]]; then
  SITES=(aiapibank pinaic)
fi

# sites/ should not be world-writable; env files 0600.
if [[ -d "$ROOT/sites" ]]; then
  chmod 700 "$ROOT/sites" 2>/dev/null || true
fi

read_poll_interval() {
  local env_file="$1"
  local val
  val="$(grep -E '^[[:space:]]*POLL_INTERVAL_SECONDS=' "$env_file" | tail -n1 | cut -d= -f2- | tr -d '[:space:]"')"
  if [[ -z "$val" ]]; then
    val=300
  fi
  if ! [[ "$val" =~ ^[0-9]+$ ]]; then
    echo "invalid POLL_INTERVAL_SECONDS in $env_file: $val" >&2
    return 1
  fi
  printf '%s' "$val"
}

check_interval_vs_timer() {
  local site="$1"
  local env_file="$2"
  local env_interval
  env_interval="$(read_poll_interval "$env_file")" || return 1
  local delta=$(( env_interval > TIMER_EXPECTED_MIDPOINT ? env_interval - TIMER_EXPECTED_MIDPOINT : TIMER_EXPECTED_MIDPOINT - env_interval ))
  if (( delta > INTERVAL_TOLERANCE_SEC )); then
    cat >&2 <<EOF
error: site=$site POLL_INTERVAL_SECONDS=$env_interval disagrees with once-timer expected midpoint ${TIMER_EXPECTED_MIDPOINT}s (tolerance ${INTERVAL_TOLERANCE_SEC}s).

  Timer template: OnUnitInactiveSec=${TIMER_ON_INACTIVE_SEC}s + RandomizedDelaySec=${TIMER_RANDOMIZED_DELAY_SEC}s
  (expected interval ≈ ${TIMER_ON_INACTIVE_SEC}s + U(0,${TIMER_RANDOMIZED_DELAY_SEC}s) + task runtime; midpoint ~${TIMER_EXPECTED_MIDPOINT}s)

  Fix options:
    1) Align env POLL_INTERVAL_SECONDS near ${TIMER_EXPECTED_MIDPOINT} (e.g. 270 or 300)
    2) Override timer for this site:
         systemctl edit sub2api-monitor-once@${site}.timer
       then re-run install after drop-in matches env
  Refusing to enable once-timer for $site.
EOF
    return 1
  fi
  return 0
}

unit_is_active() {
  local unit="$1"
  systemctl is-active --quiet "$unit" 2>/dev/null
}

enable_once_site() {
  local site="$1"
  local env_file="$ROOT/sites/${site}.env"
  if [[ ! -f "$env_file" ]]; then
    echo "skip $site: missing $env_file" >&2
    return 0
  fi
  chmod 600 "$env_file" || true

  if ! check_interval_vs_timer "$site" "$env_file"; then
    return 1
  fi

  local old_unit="sub2api-monitor@${site}.service"
  if unit_is_active "$old_unit"; then
    cat >&2 <<EOF
error: $old_unit is active; refuse dual-run with once-timer (same flock/data).

  Stop the old simple unit first, then re-run:
    systemctl stop ${old_unit}
    $0 ${site}

  Or keep simple with: $0 --legacy-simple ${site}
EOF
    return 1
  fi

  # Validate config before enabling.
  if ! "$ROOT/.venv/bin/python" "$ROOT/sub2api_monitor.py" --env-file "$env_file" --validate; then
    echo "skip $site: config validation failed" >&2
    return 1
  fi

  systemctl enable --now "sub2api-monitor-once@${site}.timer"
  systemctl --no-pager --full status "sub2api-monitor-once@${site}.timer" || true
  cat <<EOF
enabled once-timer for site=$site
  status:  systemctl status sub2api-monitor-once@${site}.timer
  logs:    journalctl -u sub2api-monitor-once@${site} -n 80 --no-pager
  now:     systemctl start sub2api-monitor-once@${site}.service
  rollback:
    systemctl disable --now sub2api-monitor-once@${site}.timer
    systemctl stop sub2api-monitor-once@${site}.service 2>/dev/null || true
    systemctl enable --now sub2api-monitor@${site}.service
EOF
}

enable_legacy_site() {
  local site="$1"
  local env_file="$ROOT/sites/${site}.env"
  if [[ ! -f "$env_file" ]]; then
    echo "skip $site: missing $env_file" >&2
    return 0
  fi
  chmod 600 "$env_file" || true

  local once_timer="sub2api-monitor-once@${site}.timer"
  if unit_is_active "$once_timer" || systemctl is-enabled --quiet "$once_timer" 2>/dev/null; then
    if unit_is_active "sub2api-monitor-once@${site}.service" || unit_is_active "$once_timer"; then
      cat >&2 <<EOF
error: once path still active for $site; stop/disable before --legacy-simple:
  systemctl disable --now sub2api-monitor-once@${site}.timer
  systemctl stop sub2api-monitor-once@${site}.service 2>/dev/null || true
EOF
      return 1
    fi
  fi

  systemctl enable --now "sub2api-monitor@${site}.service"
  systemctl --no-pager --full status "sub2api-monitor@${site}.service" || true
}

enable_models_site() {
  local site="$1"
  local env_file="$ROOT/sites/${site}.env"
  if [[ ! -f "$env_file" ]]; then
    echo "skip models $site: missing $env_file" >&2
    return 0
  fi
  if [[ ! -f "$MODELS_TIMER_DST" ]]; then
    echo "models timer template not installed" >&2
    return 1
  fi
  chmod 600 "$env_file" || true
  if ! "$ROOT/.venv/bin/python" "$ROOT/sub2api_monitor.py" --env-file "$env_file" --validate; then
    echo "skip models $site: config validation failed" >&2
    return 1
  fi
  # Do not require bootstrap_completed file here (may live only after first run);
  # ops must have bootstrapped; warn if models_latest lacks the flag.
  local data_dir
  data_dir="$(grep -E '^[[:space:]]*DATA_DIR=' "$env_file" | tail -n1 | cut -d= -f2- | tr -d '[:space:]"')"
  if [[ -z "$data_dir" ]]; then
    data_dir="$ROOT/data/${site}"
  elif [[ "$data_dir" != /* ]]; then
    data_dir="$ROOT/$data_dir"
  fi
  local models_latest="${data_dir}/models_latest.json"
  if [[ ! -f "$models_latest" ]] || ! grep -q 'bootstrap_completed_at' "$models_latest" 2>/dev/null; then
    cat >&2 <<EOF
warning: site=$site may not be bootstrapped (no bootstrap_completed_at in models_latest).
  Run preflight + bootstrap before relying on daily models:
    $ROOT/.venv/bin/python $ROOT/sub2api_monitor.py --env-file $env_file --models-preflight
    $ROOT/.venv/bin/python $ROOT/sub2api_monitor.py --env-file $env_file --models-bootstrap
  Continuing to enable timer as requested.
EOF
  fi
  systemctl enable --now "sub2api-models-daily@${site}.timer"
  systemctl --no-pager --full status "sub2api-models-daily@${site}.timer" || true
  echo "enabled models daily timer for site=$site (does not enable T-new incremental)"
}

FAILED=0
# Default path enables groups once-timers unless only models flags were used
# without site list intent — if --enable-models alone with sites, enable models;
# still enable groups unless ENABLE_MODELS is the only action requested with
# INSTALL_MODELS and user passed --enable-models only... Keep simple:
# always enable groups once (existing behavior) unless LEGACY; additionally
# enable models when --enable-models.
for site in "${SITES[@]}"; do
  if [[ "$LEGACY_SIMPLE" -eq 1 ]]; then
    if ! enable_legacy_site "$site"; then
      FAILED=1
    fi
  else
    if ! enable_once_site "$site"; then
      FAILED=1
    fi
  fi
  if [[ "$ENABLE_MODELS" -eq 1 ]]; then
    if ! enable_models_site "$site"; then
      FAILED=1
    fi
  fi
done

if [[ "$FAILED" -ne 0 ]]; then
  echo "one or more sites failed; see messages above" >&2
  exit 1
fi

echo "done. list timers: systemctl list-timers 'sub2api-monitor-once@*'"
if [[ -f "$MODELS_TIMER_DST" ]]; then
  echo "models daily templates installed; enable per site only after bootstrap:"
  echo "  $0 --enable-models <site>"
  echo "  systemctl list-timers 'sub2api-models-daily@*'"
fi
