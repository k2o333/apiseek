#!/usr/bin/env bash
# Convenience wrapper: install template and enable pinaic instance.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/install_service.sh" pinaic
